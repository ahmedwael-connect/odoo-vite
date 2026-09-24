# Odoo Vite — Sprint: Scheduled Backups & Backup Management

**Scope note:** the requested feature table includes Manual Backup and Manual Restore — **both already exist** (Sprint 5: `core/db_backup.py`, pg_dump `-Fc` + sidecar JSON metadata, restore with dump validation, already in the Databases tab and in your v2.0.0 verification checklist). Nothing to build there; if you find a real gap in the existing manual flow while working on this sprint, report it separately, don't silently rebuild it. This sprint covers only what's actually new: **Scheduled Backups, Backup Schedule Status, List Backup Files, Run Backup Now.**

**Can proceed independently of Fix Round 2** — this touches new code (`core/backup_scheduler.py`, a new UI section) and doesn't overlap with the Configuration/Logs areas Fix Round 2 is working in.

---

## Design note before starting
This is the first feature in the project that needs to **do something on a schedule while the app might not even be running** (backups shouldn't require Odoo Vite to be open at the exact moment they're due). Decide this explicitly: either (a) the app schedules via the OS itself (a `cron` entry or `systemd` timer that invokes a small standalone script/command when the GUI isn't running), or (b) scheduling only works while the app is open, checked on a timer like `get_statuses()` already does, and is honestly documented as such. Option (a) is the correct behavior for something called "Scheduled Backups" in any serious sense, but it's a real jump in complexity (writing to the user's crontab or systemd user timers, needing a way to invoke backup logic without the GUI). **Flag your recommendation back to PM before committing to one** — I have a lean but want your read on the actual implementation cost once you're looking at it.

## Ticket BK.1 — `core/backup_scheduler.py`
- Represent a schedule as: which instance, which database(s), a cron-expression-style interval, retention policy (keep last N, or keep for N days — pick one or support both, note your choice), compression setting (pg_dump's own `-Fc` is already compressed; if "compression" in the spec means something additional like gzipping the sidecar or using a different pg_dump format, clarify your interpretation).
- Store schedules in the registry (new table, e.g. `backup_schedules`) — id, instance_id, database(s), cron expression, retention config, compression config, enabled/disabled, last_run, last_status, next_run (computed from the cron expression, not stored statically — recompute when needed).
- Reuse `db_backup.py`'s existing `backup_database()` for the actual dump work — this ticket is about *scheduling and tracking* backups, not re-implementing how a backup is taken.

## Ticket BK.2 — Scheduling mechanism (resolve the design note above)
- Implement whichever approach was confirmed with PM. If OS-level (cron/systemd): needs a small standalone invocation path (e.g. `python -m odoo_vite.backup_runner --schedule-id <id>`) that can run headlessly without launching the full GTK app — this is a real new entry point, treat it with the same care as any other privileged/automated action (logging via `audit.py`, clear failure handling since nobody's watching a GUI when it runs).
- If app-timer-based: implement via the same polling pattern as `get_statuses()`, clearly documented in the UI ("runs while Odoo Vite is open" — don't let the user believe it's happening if the app is closed).

## Ticket BK.3 — Backup Schedule Status UI
- New section (likely on the Databases tab, near the existing manual Backup/Restore buttons, or its own small area) showing: configured schedules for the instance, each with next run, last run, last status (success/failure/error message if failed).
- "Run Backup Now" button per schedule — triggers `backup_database()` immediately, outside its normal schedule, updates last_run/last_status the same way a scheduled run would.

## Ticket BK.4 — List Backup Files
- Browse existing backup files (both scheduled and manual ones already created via Sprint 5) — reuse the sidecar JSON metadata already written per backup (timestamp, source db, source instance, file path) as the data source, don't build a second index; if manual backups from before this sprint don't have discoverable metadata in a consistent location, note that as a limitation rather than trying to retroactively reconstruct history that isn't there.
- UI: a simple list — filename/path, size, timestamp, source database, with an action to delete an old backup file (with confirmation, consistent with the app's established destructive-action pattern) and/or trigger a Restore directly from this list (reusing Sprint 5's restore flow).

---

## Testing expectations
- `pytest` for `backup_scheduler.py`'s cron-expression parsing and next-run calculation (a few known expressions, confirm correct next-run times), retention logic (given a set of existing backups and a retention policy, confirm the correct ones would be pruned — but see the next line).
- **Retention/pruning is destructive (deletes old backup files) — treat it with the same caution as every other destructive action in this app.** Never delete a backup file the user might need without it being a clearly configured, visible policy the user explicitly set, and log every deletion to `audit.py`.
- Manual E2E: configure a schedule with a short interval (for testing purposes — e.g. every few minutes, not a real production cadence) on a real instance, confirm it actually runs on schedule (via whichever mechanism was chosen in BK.2) and shows up correctly in Status; trigger Run Backup Now and confirm it works outside the schedule; browse List Backup Files and confirm it shows both scheduled and pre-existing manual backups correctly; delete an old backup file through the list and confirm the confirmation dialog and audit log both work.

## Out of scope
- Manual Backup/Restore — already exists, not rebuilding
- Any notification-on-failure mechanism beyond what's visible in the Status UI (e.g. email/desktop notifications) — flag as a possible future addition, not building it now unless it's trivial given whatever scheduling mechanism gets chosen

## Report-back template
```
Ticket BK.1 (backup_scheduler.py data model): [done/blocked]
Ticket BK.2 (scheduling mechanism): [done/blocked] — approach chosen + reasoning
Ticket BK.3 (Schedule Status UI + Run Now): [done/blocked]
Ticket BK.4 (List Backup Files): [done/blocked]
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
