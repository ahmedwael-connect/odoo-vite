# Odoo Vite — Sprint 3 Spec: Lifecycle Management (Start / Stop / Restart / Status)

**Depends on:** Sprint 1 + Sprint 2 (both accepted). Uses `core/registry.py`, `core/instance.py`, `core/conf_writer.py`, `core/proc.py` (streaming helper from Sprint 2), `core/events.py`.

**Carried decisions from PM Q&A:**
- First Start auto-creates the DB via `odoo-bin -i base -d <db_name> -c <conf_path>` — but per the "enterprise-grade" discussion, this now needs a **confirm-before-first-create** step and **collision handling**, not a silent auto-run. See §2.1.
- CREATEDB/least-privilege DB-role rework is **deferred** to a future hardening sprint (not this one) — do not touch `db_manager.ensure_role()`'s privilege logic in this sprint.
- We are **not** building a mode toggle (`Developer`/`Managed`) yet — noted for backlog only.

---

## Goal of this sprint
From the instance list, the user can **Start**, **Stop**, **Restart** any managed instance, see **live status** (running/stopped, PID, port, version, CPU%, memory), and configure **auto-update-on-start** modules. First Start creates the database with an explicit confirmation step.

---

## 1. Data model addition
No new table. Confirm these existing `instances` columns are used correctly (from Sprint 1/2 schema): `pid`, `status`, `port`, `primary_db`, `auto_update_modules` (JSON array).

Add one new column via `ensure_schema()`:
- `db_created BOOLEAN DEFAULT 0` — tracks whether the first-start DB creation has happened, independent of `status`, so a Stop/Start cycle later doesn't re-trigger the "create DB" confirmation flow.

---

## 2. Core logic (`core/process_manager.py` — new module)

### 2.1 `start_instance(instance_id: str, database: str | None = None, confirm_cb=None) -> Result`
Flow:
1. Load instance from registry. If already `status == "running"` (and PID verified alive), return `Result(ok=False, message="already running")`.
2. Determine target database: `database` param if given, else `instance.primary_db`, else `instance.db_name` from creation.
3. **If `db_created` is False for this instance AND this is the first-ever start:**
   - Call `confirm_cb(preview: dict) -> bool` — a callback the UI supplies, showing the exact command about to run (`odoo-bin -i base -d <db> -c <conf_path>`) and asking for explicit confirmation. If `confirm_cb` is `None` (e.g. a future CLI context) default to **not** auto-confirming — require explicit opt-in, never silently create in a headless path either.
   - If confirmed: check DB collision first — `db_manager.database_exists(db_name) -> bool`. If it already exists (e.g. retry after partial failure, or name collision with another instance), do **not** silently reuse or silently fail: return a `Result` with `data={"collision": True}` so the UI can offer "Reuse existing / Pick new name / Abort" rather than guessing.
   - Build command with `-i base -d <db_name>`.
   - If not confirmed: abort start, return `Result(ok=False, message="cancelled by user")`.
4. **Otherwise** (already created, or not first start): build command without `-i base`: `<venv>/bin/python <community_path>/odoo-bin -c <conf_path> -d <database>`.
5. If `instance.auto_update_modules` is non-empty, append `-u <comma_joined_modules>` to the command (applies on every Start/Restart, not just first).
6. Launch via `subprocess.Popen(cmd, start_new_session=True, stdout=<append to log_path>, stderr=STDOUT)`. Do not pipe stdout back for streaming in this sprint (unlike clone/pip in Sprint 2) — the odoo-bin log already writes to `log_path` per the conf file; the UI will tail that file if a live log view is wanted (nice-to-have, not required this sprint).
7. On successful launch (process didn't immediately exit — check with a short `time.sleep(1)` + `poll()` liveness check, not a guarantee of full Odoo boot, just "didn't crash instantly"): update registry — `pid`, `status="running"`, `primary_db=database`, `db_created=True`.
8. Append an entry to the audit log (see §4).
9. Return `Result(ok=True, data={"pid": ..., "port": ...})`.

### 2.2 `stop_instance(instance_id: str, timeout: int = 15) -> Result`
- Verify PID is alive (`psutil.pid_exists` + cmdline sanity check against the stored community_path/conf_path, to avoid killing an unrelated process that happens to reuse the PID).
- Send `SIGTERM`. Poll every 0.5s up to `timeout` seconds for exit.
- If still alive after timeout: `SIGKILL`, log a warning that graceful shutdown failed (Odoo should normally shut down quickly; a forced kill is notable and worth surfacing, not silently swallowing).
- Update registry: `status="stopped"`, `pid=NULL`.
- Audit log entry.

### 2.3 `restart_instance(instance_id: str, database: str | None = None) -> Result`
- `stop_instance()` then `start_instance()` with the given (or existing primary) database. Straightforward composition — no first-start confirmation logic here since `db_created` is already `True` by definition if it's running to restart.

### 2.4 `get_statuses() -> list[dict]`
- The batch call the UI polls (every ~2s via `GLib.timeout_add`).
- For every instance in the registry: if `status=="running"` in the cache, verify the PID is actually alive + cmdline matches; if not, correct the cached status to `"stopped"` (process died outside our control — crash, manual kill, OOM, etc.) and log that as a notable audit event ("instance X was running but process was gone — marked stopped").
- For genuinely-alive processes, use `psutil.Process(pid)` to pull `cpu_percent()`, `memory_info().rss`, and return a merged dict per instance: `{id, name, status, pid, port, version, cpu_percent, memory_mb}`.
- This function must be **fast and non-blocking-safe** to call every 2 seconds — avoid anything that hits disk/network per call beyond the lightweight psutil calls. Run it in a background thread from the UI, not directly on the GTK main loop, same pattern as before.

---

## 3. UI (`ui/page_instance_list.py`, `ui/page_instance_detail.py` — new)

### 3.1 Instance list — enhance from the Sprint 1 stub
- Each row: name, version, status pill (colored: green=running, grey=stopped, red=error), port, quick actions (Start/Stop/Restart icon buttons — context-sensitive, e.g. Start hidden while running).
- Poll `get_statuses()` on a `GLib.timeout_add(2000, ...)` and update rows in place (don't rebuild the whole list every tick — diff and update, to avoid UI flicker/scroll-jank).

### 3.2 Instance detail page (new)
- Header: name, version, status, Start/Stop/Restart buttons.
- Stats: PID, port, CPU%, memory, primary database.
- **Auto-update-on-start** section: multi-select or comma-entry of module names, saved to `instance.auto_update_modules` via `registry.update_instance`. (No live validation against actual installed modules this sprint — that requires talking to the DB/Odoo itself, out of scope; just accept free-text module names, comma-separated, trimmed.)
- Link/button to open `http://localhost:<port>` in the default browser (`Gtk.UriLauncher` or `webbrowser.open`) once running — small but high-value convenience.
- **First-start confirmation dialog**: `Adw.AlertDialog` (or `Adw.MessageDialog` depending on libadwaita version available) showing the exact command preview from §2.1 step 3, with Confirm/Cancel. On DB collision, a second dialog offering Reuse/New name/Abort per §2.1.

---

## 4. Audit log (`core/audit.py` — new, small)
- Plain append-only log at `~/.local/share/odoo-vite/audit.log`, one JSON line per event: `{"ts": ISO8601, "instance_id": ..., "instance_name": ..., "action": "start"|"stop"|"restart"|"db_create"|"forced_kill", "detail": "..."}`.
- Called from `process_manager.py` at each of the points noted in §2.1/2.2. Keep this dead simple — no rotation/size limits needed yet, just get events flowing; note in your report if you think rotation is needed already (probably not yet, but flag if you disagree).

---

## 5. Testing expectations
- `pytest` for `process_manager.py` logic that doesn't require a real Odoo process: mock `subprocess.Popen`/`psutil`, assert command construction (with/without `-i base`, with/without `-u modules`), assert collision handling returns the right `Result` shape, assert `get_statuses()` correctly downgrades a cached "running" instance whose PID is gone.
- Manual E2E on real Ubuntu using the Sprint 2 instance already provisioned: Start (confirm first-start dialog appears, DB gets created), verify `http://localhost:<port>` loads Odoo's login/setup screen, Stop (verify graceful SIGTERM works, process actually exits), Restart, and a forced-kill scenario (kill -9 the process manually outside the app, confirm `get_statuses()` correctly detects and reports it as stopped within ~2-4s).

## 6. Out of scope for Sprint 3
- Adopt/Remove/Switch DB/Track DB/Set Primary DB (Sprint 4)
- Live-tailing the odoo.log file in the UI (nice-to-have, punt unless trivial)
- DB-role least-privilege rework (deferred hardening sprint, not now)
- Module validation against what's actually installed in the target DB

## 7. Report-back template
```
Ticket 3.1 (schema addition: db_created): [done/blocked] notes...
Ticket 3.2 (process_manager.start_instance incl. confirm+collision flow): [done/blocked]
Ticket 3.3 (process_manager.stop_instance): [done/blocked]
Ticket 3.4 (process_manager.restart_instance): [done/blocked]
Ticket 3.5 (process_manager.get_statuses): [done/blocked]
Ticket 3.6 (instance list live polling UI): [done/blocked]
Ticket 3.7 (instance detail page + auto-update config + confirm dialogs): [done/blocked]
Ticket 3.8 (audit.py): [done/blocked]
Manual E2E run: [pass/fail] + what was verified (incl. forced-kill detection test)
Deviations from architecture: ...
Open questions for PM: ...
```
