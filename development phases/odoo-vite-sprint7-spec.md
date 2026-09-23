# Odoo Vite — Sprint 7 Spec: Configuration Management

**Depends on:** Sprints 1–6 + all hardening/RC/hotfix sprints (assume accepted pending your human pass — if that pass turns up blockers, this sprint waits, per our standing rule).

**Note on scope:** the requested table includes **Set Primary Database** — that's already built (Sprint 4, still working per Sprint 5/6 testing). No new ticket for it here; I'm listing it below only to confirm it's covered, not re-specifying it.

---

## Design principles for this sprint (read before starting)

This phase is different in character from everything before it: for the first time, the app is editing **the actual file Odoo reads to boot** — for a real, possibly-running instance. Get this wrong and the failure mode isn't "a dialog looks weird," it's "an instance won't start and the user doesn't know why." Three rules apply to every ticket below:

1. **Never write a conf file that doesn't parse or that's missing a value Odoo requires.** Validate before writing, not after. If validation fails, refuse to save and say exactly why — don't leave a half-written file.
2. **Always back up the previous conf before overwriting it** — one rolling backup (`odoo.conf.bak`) is enough for v1, doesn't need full versioning. If a bad edit gets saved anyway (validation isn't infallible), the user has a one-click way back.
3. **Editing a running instance's conf does not live-reload it.** Odoo reads its conf at process start. Any edit made while the instance is running must be clearly labeled "takes effect on next restart" in the UI — don't imply it's instant when it isn't.

---

## Ticket 7.1 — Read odoo.conf
- `core/conf_manager.py` (new — or extend `conf_writer.py` from Sprint 2 if that's a cleaner home; your call, but don't duplicate the ini-parsing logic that already exists in `adopt.py`'s `parse_conf()` — consolidate into one shared parser both modules call, same "single source of truth" lesson as `db_state.py`).
- `read_conf(conf_path: str) -> Result` — returns the full key-value dict from `[options]`, plus any other sections present (some confs have `[queue_job]` or similar from installed modules — read and preserve them even though we don't actively manage them, don't silently drop unknown sections on save).
- UI: new "Configuration" tab on the instance detail page (alongside Overview/Databases/Modules from Sprint 6), showing the current conf as a key-value table, read-only view by default.

## Ticket 7.2 — Edit odoo.conf
- `update_conf_keys(conf_path: str, changes: dict) -> Result` — apply a set of key changes, re-serialize, validate (re-parse the result and confirm it's well-formed + confirm no required key was accidentally emptied), back up the previous version, write.
- UI: inline-editable fields on the Configuration tab for common/expected keys (`db_host`, `db_port`, `db_user`, `xmlrpc_port`/`http_port`, `logfile`, `addons_path` — though `addons_path` gets its own dedicated editor per Ticket 7.4, don't build two separate edit paths for the same key, this table view should show it read-only with a link/button to the Addon Path Manager instead). For keys not in the common set, provide a generic "add/edit raw key" affordance so power users aren't blocked from editing something we didn't anticipate — but validate generic edits the same way (well-formed ini, no silently broken file).
- "Save" button, with the "takes effect on next restart" notice shown whenever the instance is currently running at save time.

## Ticket 7.3 — Create odoo.conf (regenerate from registry)
- Reuses Sprint 2's `conf_writer.write_conf()` — this ticket is really "expose the existing regeneration capability as an explicit, user-triggered action," not new core logic.
- UI: "Regenerate from current settings" button (Configuration tab, clearly separated from the edit form, maybe in a small "Advanced" section) — this **overwrites manual edits** with a fresh conf built purely from registry fields (port, db user, addons paths, etc.), so it needs its own confirmation, distinct from the regular Save confirmation, explicitly warning that manual key edits not reflected in the registry will be lost.
- Use case: recovering from a manually-corrupted conf, or after registry fields changed elsewhere (e.g. Set Primary Database) and the user wants the conf file to match exactly.

## Ticket 7.4 — Addon Path Manager
- The most structurally involved ticket in this sprint. `addons_path` is a single comma-separated string in the conf, but the feature asks for add/remove/toggle/reorder/move — meaning we need our own structured representation on top of it, not just string editing.
- `core/addon_paths.py` (new): represent the addons path as an ordered list of `{path: str, enabled: bool}` entries. "Toggle" means a path is temporarily excluded from the written `addons_path` without deleting it from the list (useful for debugging "which addon folder is causing this" without losing the reference) — store this structured list in the registry (new column or JSON field on `instances`, your call) as the source of truth, and **derive** the conf's `addons_path` string from it (enabled entries only, in order) rather than the other way around. This avoids the conf file and the registry disagreeing about order/state.
- Operations: Add (folder picker, validated the same way Sprint 2's enterprise-folder check works — warn if it doesn't look like an addons folder, don't hard-block), Remove, Toggle enabled/disabled, Reorder (up/down or drag — pick whichever is simpler to implement correctly in GTK4, drag-and-drop reordering has more edge cases, note your choice), Move is likely the same operation as Reorder — confirm with the feature table's intent or treat as synonymous, don't build two mechanisms for one concept.
- Every change re-derives and re-writes `addons_path` in the conf (through Ticket 7.2's validated write path, not a separate one) and shows the same "takes effect on next restart" notice.
- Existing instances created before this ticket won't have the structured list yet — on first opening the Addon Path Manager for such an instance, parse the existing `addons_path` string into the structured list (all entries enabled, in their current order) as a one-time migration, don't require the user to manually re-enter paths they already had.

## Ticket 7.5 — Update Instance Metadata
- Extend the registry schema (additive, via `ensure_schema()`, same pattern as every prior migration) with: `description TEXT NULL`, `workers INTEGER DEFAULT 0`, `log_level TEXT DEFAULT 'info'`, `python_binary TEXT NULL` (defaults to the venv's own python — this only matters for adopted instances or unusual setups where a different interpreter is intentional; reuse/rename the "Python environment" field already shown on adopted instances from Sprint 6 rather than building a second, separate field for the same concept — audit and consolidate if they've drifted apart).
- `workers` and `log_level` are real Odoo conf keys (`workers`, `log_level`) — writing them goes through Ticket 7.2's validated conf-write path, same as any other key. `description` is registry-only, has no conf equivalent, purely for the user's own organization (shown on the instance list/detail as a subtitle or note).
- UI: a "Metadata" section on the Configuration tab (or its own small form) — description (free text), workers (number, with a brief inline explanation of what it does — 0 means single-process/dev mode, which is likely the default every instance has been running under so far; note if enabling multi-worker mode has any other prerequisites, like needing `max_cron_threads` or a `longpolling_port`/`gevent_port`, and surface those together rather than letting a user set `workers>0` and hit a confusing failure), log level (dropdown of Odoo's actual accepted values, not free text), python binary path (file picker, validated it's an actual executable before accepting).

## Ticket 7.6 — Set Primary Database (confirmation only, no new work expected)
- Already implemented (Sprint 4). Confirm it's still working correctly given everything else that's shipped since (Sprint 5's `db_state.py` consolidation, Sprint 6's Modules tab) — a quick regression check, not new development. If you find it's drifted or has a gap, report it, don't silently patch it outside this note.

---

## Testing expectations
- `pytest` for the shared conf parser (round-trip: read → write unchanged → read again, byte-for-byte or semantically identical), for `update_conf_keys` validation (rejects a would-be-broken write, accepts a good one, confirms backup file is created), and for `addon_paths.py`'s derive-string-from-structured-list logic (order preserved, disabled entries excluded, migration-from-existing-string produces the expected structured list).
- Manual E2E: edit a conf key on a stopped instance, confirm it's written correctly and Start still works afterward; edit a conf key on a **running** instance, confirm the "next restart" notice appears and the live process is unaffected until restarted; use the Addon Path Manager to disable a path, restart, confirm Odoo no longer loads modules from that path; regenerate from registry on an instance with manual edits, confirm the warning appears and the regenerated file is correct; set workers>0 and confirm either it works correctly or the UI surfaced the right prerequisite warning before the user could hit a confusing failure.

## Out of scope for Sprint 7
- Live-reloading a running instance's conf without a restart (Odoo doesn't support this natively for most keys; not our job to work around that)
- Any new database or module features — this sprint is conf/metadata only

## Report-back template
```
Ticket 7.1 (Read odoo.conf + shared parser consolidation): [done/blocked]
Ticket 7.2 (Edit odoo.conf, validated write + backup): [done/blocked]
Ticket 7.3 (Create/regenerate odoo.conf): [done/blocked]
Ticket 7.4 (Addon Path Manager): [done/blocked] — reorder mechanism chosen, migration handling
Ticket 7.5 (Instance metadata: description/workers/log_level/python_binary): [done/blocked] + worker-mode prerequisite handling
Ticket 7.6 (Set Primary DB regression check): [pass/issue found]
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
