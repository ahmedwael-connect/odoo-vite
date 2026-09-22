# Odoo Vite — Sprint 4 Spec: Adopt / Remove / Switch DB / Track DB / Set Primary DB

**Depends on:** Sprints 1–3 (all accepted). Uses `core/registry.py`, `core/process_manager.py`, `core/conf_writer.py`, `core/db_manager.py`, `core/audit.py`.

**Carried decisions from PM Q&A:**
- Adopt-flow conf parsing is **lenient**: report present/missing required fields individually, never invent a value for a missing one — surface it as an editable field instead of rejecting the import.
- Notifications: **toast** for resolved/transient events, **persistent label + status pill** for ongoing bad states — apply this pattern to Remove/Switch-DB failures too, not just Sprint 3's start/stop.
- This sprint completes the Phase 1 feature set as originally scoped (§6/§7 of the original charter). No new architecture decisions expected — flag immediately if one comes up.

---

## Goal of this sprint
A user can bring an **existing** Odoo install under Odoo Vite's management without re-provisioning, safely **remove** any instance (managed or adopted, with correct semantics for each), **switch** which database an instance serves, and **track/curate** the list of databases associated with an instance.

---

## 1. Data model
No new columns expected. Confirm `mode` (`"managed"`|`"adopted"`), `tracked_dbs` (JSON array), `primary_db` are already present from Sprint 1's schema and correctly read/written.

---

## 2. Adopt Instance

### 2.1 `core/adopt.py` (new)
- `parse_conf(conf_path: str) -> dict` — parse the ini-style Odoo conf into a plain dict of key→value from `[options]`.
- `validate_adopted_conf(parsed: dict) -> dict` — returns `{field: "present"|"missing"}` for the required-for-us set: `addons_path`, `db_user`, `db_password` (optional — may be blank if using peer auth, don't hard-require), `xmlrpc_port`/`http_port`, `logfile`. Lenient per PM decision — this never raises, just reports.
- `adopt_instance(name: str, conf_path: str, community_path: str, overrides: dict) -> Result` — builds an `Instance` with `mode="adopted"`, `path=<parent dir inferred or user-specified>`, fields taken from the parsed conf merged with any user-supplied `overrides` (for fields flagged missing), `status` determined by an immediate `get_statuses()`-style live check (an adopted instance might already be running when imported — don't assume stopped). **Does not touch any files** — no clone, no venv creation, no conf rewrite. Registers the row only.
- Validate `name` uniqueness same as Sprint 2's create flow.

### 2.2 UI — Adopt wizard (`ui/wizard_adopt_instance.py`, new)
Steps:
1. **Locate**: file pickers for the Odoo conf file (required) and the community `odoo-bin` folder (required — used to determine version by reading `odoo-bin --version` or parsing `release.py` in the source, whichever is more reliable; note which you used).
2. **Review & fill gaps**: show `validate_adopted_conf()` results — present fields read-only-ish (still editable if user wants to override), missing fields as required input boxes. Also let the user optionally point at an enterprise addons folder and a custom addons folder if not already inferable from `addons_path`.
3. **Confirm**: summary, "Adopt" button → `adopt_instance()`.
4. On success: instance appears in the list immediately, with correct live status (don't force it to "stopped" if it's actually running — check).

---

## 3. Remove Instance

### 3.1 `core/removal.py` (new)
- `remove_instance(instance_id: str, drop_db: bool = False) -> Result`
  - Load instance. If `status == "running"`, stop it first (reuse `process_manager.stop_instance`) — never delete files or drop a DB out from under a running process.
  - **If `mode == "managed"`:**
    - Delete `instance.path` recursively (`shutil.rmtree`).
    - If `drop_db` is True: `db_manager.drop_database(db_name)` — **only** the `primary_db`/tracked dbs that this instance created, never touch databases not associated with this instance.
    - Delete the registry row.
  - **If `mode == "adopted"`:**
    - **Never** touch any files or databases, regardless of `drop_db` — an adopted instance's files/data are owned by the user, not by us. If `drop_db=True` is passed for an adopted instance, treat it as a no-op and log/return a note explaining why, rather than silently ignoring it (surface: "drop_db ignored — instance is adopted, Odoo Vite does not manage its data").
    - Just delete the registry row ("unregister").
  - Audit log entry either way (`action: "remove"`, `detail` noting mode + whether files/db were touched).

### 3.2 UI — Remove confirmation
- **Managed instance:** `Adw.AlertDialog` requiring the user to **type the instance name** to confirm (classic "type to confirm" destructive-action pattern) — this is deleting real files and optionally a real database, treat it with that seriousness. Checkbox: "Also drop the database `<db_name>`" (unchecked by default — don't default to destructive).
- **Adopted instance:** lighter confirmation ("This will remove `<name>` from Odoo Vite. Your files and database will not be touched.") — no type-to-confirm needed, since nothing destructive happens on disk.

---

## 4. Switch Database / Set Primary Database

### 4.1 Core logic
- **Set Primary Database** (no restart): `registry.update_instance(id, primary_db=new_db)` only. This just changes what a *future* Start/Restart will default to — does not affect a currently running instance.
- **Switch Database** (live): `process_manager.stop_instance(id)` → `process_manager.start_instance(id, database=new_db)` → on success, this also updates `primary_db` (reuse Sprint 3's `start_instance`, which already sets `primary_db=database` on success — confirm this, no new logic needed beyond calling it with the new db name). If `new_db` doesn't exist yet, this should hit the same **first-time-per-database** consideration as Sprint 3's confirm/collision flow — reuse that logic rather than duplicating it (a "new to this instance" database should get the same confirm-before-create treatment, not silently `-i base` it).

### 4.2 UI
- On the instance detail page: a database dropdown/selector populated from `tracked_dbs` (+ "Other..." free-text entry for a db not yet tracked). Two actions next to it: **Set as Primary** (no restart, instant) and **Switch Now** (stops+restarts if currently running; if currently stopped, this is equivalent to Set Primary — no need to start it as a side effect, don't surprise the user by starting a stopped instance just because they picked a db).

---

## 5. Track Database

### 5.1 Core logic
- `db_manager.list_databases_for_user(db_user: str) -> Result` — queries Postgres for databases owned by/accessible to `db_user` (e.g. `psql -U <user> -l` parsed, or a proper `SELECT datname FROM pg_database WHERE ...` via `psycopg2`/`subprocess psql -tAc`). Pick whichever is more robust given what's already used elsewhere in `db_manager.py` for consistency — note your choice.
- `track_database(instance_id: str, db_name: str) -> Result` — appends to `tracked_dbs` JSON array if not already present (idempotent, no duplicates).
- `untrack_database(instance_id: str, db_name: str) -> Result` — removes from the array. If `db_name == primary_db`, this is allowed (untracking doesn't require it to not be primary) but should prompt a warning in the UI ("this is the current primary database — untracking won't change what's running, but it'll disappear from the switch-db list").

### 5.2 UI
- On the instance detail page, near the database selector: a small "Discover databases" button that runs `list_databases_for_user()` and shows a checklist of found-but-not-yet-tracked databases, letting the user tick which ones to add to `tracked_dbs`. Manual add-by-name entry also available (per the original feature description: "manually add a discovered DB").

---

## 6. Testing expectations
- `pytest` for `adopt.py`: parsing a sample conf string with all fields present, and a second sample with deliberately missing fields, asserting `validate_adopted_conf()` correctly flags each. Also test `adopt_instance()` never invokes any file-writing/cloning functions (mock and assert not called).
- `pytest` for `removal.py`: managed vs adopted branches, assert adopted never calls `shutil.rmtree` or `drop_database` even when `drop_db=True` is passed, assert managed does call them appropriately, assert running instance is stopped first (mock `stop_instance`, assert called before file deletion).
- `pytest` for `track_database`/`untrack_database` idempotency (no duplicate entries, removal of nonexistent entry is a safe no-op).
- Manual E2E: (1) adopt a real pre-existing Odoo folder+conf (can reuse a Sprint-2-provisioned instance's files as a stand-in "existing install" by adopting it under a different name, to avoid needing a truly separate install), confirm it shows correct live status; (2) remove a managed instance with drop_db checked, confirm files and DB both gone; (3) remove an adopted instance, confirm files/DB untouched, only the registry row disappears; (4) switch a running instance to a second tracked database and confirm it actually serves that db afterward (check via the browser link); (5) track a manually-created Postgres database via "Discover".

## 7. Out of scope for Sprint 4
- Anything beyond what's listed above — this closes out the originally-scoped Phase 1 feature list. Do not start on addon management, backups, or any Phase 2 idea without a new spec from PM.
- The deferred least-privilege DB role rework (still on backlog, still not this sprint).

## 8. Report-back template
```
Ticket 4.1 (adopt.py: parse/validate/adopt_instance): [done/blocked] notes...
Ticket 4.2 (Adopt wizard UI): [done/blocked] notes...
Ticket 4.3 (removal.py: managed vs adopted semantics): [done/blocked] notes...
Ticket 4.4 (Remove confirmation UI incl. type-to-confirm): [done/blocked] notes...
Ticket 4.5 (Set Primary / Switch Database core+UI): [done/blocked] notes...
Ticket 4.6 (Track/Untrack/Discover databases core+UI): [done/blocked] notes...
Manual E2E run: [pass/fail] + what was verified (all 5 scenarios)
Deviations from architecture: ...
Open questions for PM: ...
```
