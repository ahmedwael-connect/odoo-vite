# Odoo Vite — Sprint 5 Spec: Database Ground-Truth Consolidation + Database Operations

**Depends on:** Sprints 1–4 + Phase 1.5 + RC + Hotfix sprints (all accepted). This sprint has two parts: **Part A is mandatory and comes first** — it fixes a recurring architectural gap. **Part B (the new Database Operations feature set) builds directly on Part A** — don't start B's UI work until A's core module exists, or you'll be building on the same shaky ground that caused this.

---

## Part A — Single source of truth for database existence/state (mandatory prerequisite)

### Why this is happening a third time
- RC sprint BUG-3: `db_created` flag trusted over reality in the create/collision flow.
- Hotfix H-B3: Remove trusted a flag when deciding whether to drop.
- Just reported: the Start confirmation dialog itself offers to "create" `ro_website_test_db` even though it's already tracked and was created for this instance.

Each fix so far solved its own local spot. That's the problem — there is no one place in the codebase that owns the answer to "does this database exist, and is it actually initialized," so every feature that needs that answer re-implements its own version, and some of those versions are wrong or stale. This sprint fixes the actual gap.

### Ticket A.1 — `core/db_state.py` (new, authoritative module)
- `get_db_state(db_name: str, db_user: str, db_password: str) -> DbState` — a dataclass/`Result.data` shape with at minimum: `exists: bool`, `initialized: bool` (has `ir_module_module` with `base` installed), `odoo_version: str | None` (read from the `ir_module_module` row for `base`, or wherever Odoo records it — confirm the right source per major version, it may differ slightly), `size_bytes: int | None`, `owner: str | None`.
- This function does the actual Postgres query work (reuse/consolidate whatever partial logic already exists in `db_manager.py`'s `database_exists`/`database_initialized` — merge them into this one call rather than keeping both alive separately).
- **No caching inside this function** — it always asks Postgres directly. Callers that need to avoid hammering the DB on a tight UI loop should cache at the call site with an explicit TTL, not push that responsibility into the source-of-truth function itself.

### Ticket A.2 — Audit and replace every existing caller
- Grep the codebase for every place currently deciding database existence/init state — this includes at minimum: `start_instance`'s confirm/create logic, `switch_database`, `removal.py`'s drop logic, and the `database_exists`/`database_initialized` functions themselves (which should become thin wrappers around `db_state.py` or be removed in favor of it, your call, but don't leave two independent implementations answering the same question).
- Replace each with a call to `get_db_state()`. The Start confirmation dialog specifically must call this **before** rendering its "this will create X" text — right now it appears to be deciding what to show based on the `db_created` flag or a stale check rather than asking Postgres in that moment.
- **Test:** write one shared test fixture/scenario (a real or mocked Postgres database that exists-and-is-initialized) and assert that Start, Switch, and Remove all agree on its state when queried in the same moment — this is the regression test that would have caught all three bugs, write it now so a fourth occurrence is structurally prevented, not just spot-checked away.

---

## Part B — Database Operations feature set

### Ticket B.1 — List Databases (UI: new "Databases" tab on instance detail page)
- For every database in `tracked_dbs` (plus `primary_db` if somehow untracked), call `get_db_state()` and render a table: name, size (human-readable), owner, initialized (yes/no), version (if detected), primary (badge if it's the current primary).
- This becomes the **new home for Discover's filtering problem**: when a database's `get_db_state()` reports `initialized=True` with a detected `odoo_version` that doesn't match the instance's version, show a clear mismatch badge — same visual pattern as the existing enterprise-addon version-mismatch badge from Phase 1.5, for consistency.

### Ticket B.2 — Discover Databases: fix using Part A
- Rework the existing Discover dialog (Sprint 4 + Hotfix H-P1) to call `get_db_state()` for each untracked database found and **group** the results: "Likely this version" (initialized + version matches) at top, "Other Odoo databases" (initialized, different version) below, "Uninitialized / non-Odoo" (exists but no `ir_module_module`, or genuinely not an Odoo db — e.g. `postgres`, other apps' databases) collapsed/at the bottom by default.
- **Do not hide or exclude anything** — grouping, not filtering. The original "show everything the user owns" principle was right in spirit, it was just presented with zero structure. Keep the search box and scroll cap from H-P1, add grouping on top.

### Ticket B.3 — Initialize Database (explicit action)
- New action on the Databases tab: for a tracked, `initialized=False` database, an "Initialize" button that runs the same `-i base` flow already used internally by first-Start, but as a standalone, explicit action decoupled from Start — e.g. so a user can pre-create/init a database for an instance that isn't running yet, or re-init one that got into a bad state (reusing the poisoned-DB detection/reinit-confirm pattern already built in the RC sprint).
- Reuse `provisioning`/`process_manager`'s existing command-building logic for this rather than writing a second implementation of "how do we run `-i base`."

### Ticket B.4 — Drop Database (standalone, protected)
- New action on the Databases tab: "Drop" per non-primary database row. **The current primary database must be structurally un-droppable from this UI** — don't rely on a confirmation dialog alone to prevent it, disable/hide the Drop action entirely on the primary row.
- Confirmation: type-to-confirm the database name, consistent with the existing Remove Instance destructive-action pattern.
- Uses `get_db_state()` first to confirm it actually exists before attempting the drop (same lesson as Part A — don't let this feature reintroduce the bug it exists to fix).

### Ticket B.5 — Backup Database
- `core/db_backup.py` (new): `backup_database(db_name, dest_path, progress_cb) -> Result` using `pg_dump` (format: custom `-Fc`, recommended for `pg_restore` flexibility later — confirm this is right for our restore approach in B.6 before locking it in).
- UI: "Backup" button per database row, `Gtk.FileDialog` to choose destination, progress feedback (reuse the established spinner/log-pane pattern, not a new one).
- Store basic backup metadata (timestamp, source db, source instance, file path) somewhere lightweight — either a small `backups` table in the registry or a sidecar JSON next to the dump file, your call, note which and why.

### Ticket B.6 — Restore Database
- `core/db_backup.py`: `restore_database(dump_path, target_db, progress_cb) -> Result`.
- **Validate the dump first**, per the feature spec — at minimum confirm the file looks like a valid `pg_dump` output (check format header/magic bytes for custom-format dumps, or a sane pre-check for plain-SQL dumps) before attempting a restore, and fail clearly rather than letting `pg_restore` fail confusingly halfway through.
- Restoring into an existing database with data in it is destructive — same type-to-confirm pattern as Drop, and clearly state whether this restore will drop-and-recreate the target or restore into it as-is (decide which behavior is correct/safer, document the choice, don't leave it ambiguous to the user or to yourself mid-implementation).

### Ticket B.7 — Validate DB Config
- New action/section: compare the instance's `odoo.conf` `db_*` settings (`db_host`, `db_port`, `db_user`, `db_name`/primary) against Postgres' actual live state (does that user actually exist with those credentials, does it actually have the access the conf assumes) and report drift — e.g. conf says `db_user=odoo` but the role was since renamed/dropped outside the app, or password in conf/keyring no longer matches what Postgres has.
- Present as a simple "Validate" button producing a pass/fail-per-field report, not a background/automatic check — this is a diagnostic tool the user reaches for when something's wrong, not a constant poller.

---

## Testing expectations
- Part A's shared fixture/test (per Ticket A.2) is the most important test in this sprint — treat it as the actual regression guard for the whole bug class, not just a nice-to-have.
- `pytest` coverage for `db_state.py` itself (mocked Postgres responses: exists+initialized, exists+uninitialized, doesn't exist, version detection).
- `pytest` for backup/restore's dump validation logic (valid header, corrupt/truncated file, wrong format).
- Manual E2E: reproduce **both** of the just-reported bugs specifically and confirm they're gone (Discover grouping showing structure instead of a flat dump; Start no longer offering to "create" an already-existing, already-initialized database), plus one full pass through List/Initialize/Drop/Backup/Restore/Validate on a real instance.

## Out of scope for Sprint 5
- Anything beyond the table given — no new unrelated features.
- Scheduled/automatic backups (this is manual, on-demand only, for now — worth noting as a natural Phase 3 candidate but don't build it yet).

## Report-back template
```
Part A.1 (db_state.py): [done/blocked]
Part A.2 (caller audit + shared regression fixture): [done/blocked] — which
  callers were found and replaced, listed explicitly
Ticket B.1 (List Databases): [done/blocked]
Ticket B.2 (Discover grouping fix): [done/blocked]
Ticket B.3 (Initialize Database): [done/blocked]
Ticket B.4 (Drop Database, primary-protected): [done/blocked]
Ticket B.5 (Backup): [done/blocked]
Ticket B.6 (Restore + dump validation): [done/blocked]
Ticket B.7 (Validate DB Config): [done/blocked]
Manual E2E: [pass/fail] incl. explicit reproduction of both just-reported bugs
Deviations: ...
Open questions for PM: ...
```
