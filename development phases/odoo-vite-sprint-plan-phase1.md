# Odoo Vite — Project Charter & Sprint Plan (Phase 1)

**Role:** Technical Project Manager (Claude) → Developer Agent (open code muse spark 1.3)
**Platform:** Ubuntu Desktop
**Stack:** Python 3.11+, PyGObject (GTK 4), libadwaita (recommended for native Ubuntu look), SQLite (registry), PostgreSQL (odoo db backend), systemd-run or subprocess+psutil (process lifecycle)
**Methodology:** Agile, 1-week sprints, each sprint ends with a demo-able increment + a written report back to PM.

---

## 0. Product Vision

**Odoo Vite** is a native GTK4 Ubuntu desktop application that manages the full lifecycle of local Odoo instances — similar in spirit to "Docker Desktop" but purpose-built for Odoo. It replaces manual `git clone` + `venv` + `postgres` + `odoo.conf` juggling with a guided GUI, and exposes ongoing lifecycle controls (start/stop/status/db switching) for every instance it manages.

Phase 1 (this document) covers: **Instance Creation Wizard** + **Instance Lifecycle Management**. Later phases (not in this doc) will cover: addon manager, backups/restore, module upgrades, multi-instance dashboards, notifications, themes, etc.

---

## 1. Architecture Decisions (locked for Phase 1 — do not deviate without flagging to PM)

### 1.1 Layered design
Strict separation between UI and logic. The developer must NOT put subprocess/git/db logic inside GTK widget callback functions.

```
odoo_vite/
├── main.py                  # GTK Application entrypoint
├── ui/                      # GTK4 views/widgets only. No business logic.
│   ├── window_main.py
│   ├── wizard_create_instance.py
│   ├── page_instance_list.py
│   ├── page_instance_detail.py
│   └── widgets/
├── core/                    # Pure Python backend logic, framework-agnostic
│   ├── registry.py          # SQLite-backed instance registry (CRUD)
│   ├── instance.py          # Instance dataclass/model
│   ├── system_check.py      # OS/dependency detection & installer
│   ├── git_manager.py       # Odoo repo clone/branch operations
│   ├── conf_writer.py       # odoo.conf generation
│   ├── db_manager.py        # Postgres role/db creation, listing dbs
│   ├── process_manager.py   # start/stop/restart/status of odoo-bin procs
│   └── events.py            # Simple pub/sub (GLib.idle_add bridge to UI)
├── data/
│   └── odoo_vite.db          # SQLite registry (created at runtime, ~/.local/share/odoo-vite/)
└── tests/
```

**Why:** Everything under `core/` must be independently testable with `pytest`, with zero GTK imports. The `ui/` layer calls `core/` and marshals results back to the main thread via `GLib.idle_add()`. Long-running operations (clone, install, system checks) **must** run in a background thread (`threading.Thread` or `GLib.Thread`) — never block the GTK main loop. Progress must be reported via callbacks/events so the UI can show progress bars/log streams.

### 1.2 Instance Registry (SQLite schema — Sprint 1 deliverable)

Table `instances`:

| column | type | notes |
|---|---|---|
| id | TEXT (uuid4) PK | |
| name | TEXT UNIQUE | user-facing instance name |
| version | TEXT | e.g. "17.0" |
| mode | TEXT | `managed` or `adopted` |
| path | TEXT | root folder of the instance |
| venv_path | TEXT | |
| community_path | TEXT | |
| enterprise_path | TEXT NULL | |
| custom_addons_path | TEXT | |
| conf_path | TEXT | |
| log_path | TEXT | |
| port | INTEGER | |
| db_user | TEXT | default `odoo` |
| db_password | TEXT | stored via `keyring`/libsecret if possible; SQLite fallback flagged as TODO security item |
| primary_db | TEXT | which db this instance serves by default |
| tracked_dbs | TEXT (JSON array) | |
| auto_update_modules | TEXT (JSON array) | modules to `-u` on start |
| status | TEXT | `stopped`/`running`/`error` (cache only — live truth comes from process_manager) |
| pid | INTEGER NULL | |
| created_at | TEXT (ISO8601) | |

> Hint to dev: use `dataclasses` for the `Instance` model in `core/instance.py`, with a `to_row()`/`from_row()` mapper, not a raw ORM — keep it dependency-light. `sqlite3` from stdlib is sufficient.

### 1.3 Process lifecycle model
- Each running instance = one subprocess: `<venv>/bin/python <community_path>/odoo-bin -c <conf_path> [--database <db>] [-u <modules>]`
- Use `subprocess.Popen` with `start_new_session=True` so instances survive if the GUI briefly hiccups, but **the app is the source of truth for what it started** — store `pid` in the registry on start, verify liveness with `psutil.pid_exists()` + cmdline match on `Statuses()`.
- `Statuses()` = one batch function in `process_manager.py` that iterates all registry rows, checks real OS state, updates cached `status`/`pid`/`memory`/`cpu`, and returns the list — the UI calls this on a timer (e.g. every 2s via `GLib.timeout_add`) to refresh the instance list view.

### 1.4 Git/branch handling
- Use `git ls-remote --heads https://github.com/odoo/odoo` to list branches (no full clone needed) for the version-picker/search box.
- Actual clone: `git clone --branch <version> --depth 1 https://github.com/odoo/odoo <community_path>` (shallow clone — faster, sufficient for running).
- Run clone via `subprocess`, stream stdout/stderr line-by-line into the events/log system so the wizard can show a live log pane.

### 1.5 Error philosophy
Every `core/` function that can fail returns a `Result`-style object (`ok: bool, message: str, data: Any`) rather than raising raw exceptions into the UI layer. UI always has something user-readable to show.

---

## 2. Definition of Ready / Definition of Done (applies to every sprint)

**Definition of Ready** for a ticket: acceptance criteria understood, which `core/` module it lives in is clear, no unresolved dependency on a not-yet-built module.

**Definition of Done** for a ticket:
- [ ] Code lives in the correct layer (`core/` vs `ui/`)
- [ ] No blocking calls on the GTK main thread
- [ ] Errors surfaced to UI via `Result` objects, never a silent crash
- [ ] Manual test steps in the ticket have been run on real Ubuntu and pass
- [ ] Screenshot or short screen recording attached to the sprint report
- [ ] Any deviation from this doc's architecture is called out explicitly in the report, not silently done

---

## 3. Sprint 1 — Foundations + System Check + Repo/Branch Picker

**Goal:** App launches, main window shell exists, user can pick an Odoo version from live GitHub branches, and the app can audit the system for that version's prerequisites.

### Ticket 1.1 — Project scaffold & main window shell
- Set up the folder structure from §1.1, a `pyproject.toml`/`requirements.txt` (`PyGObject`, `psutil`, `keyring`, `pytest`).
- `main.py`: a `Gtk.Application` + `Adw.ApplicationWindow` (use libadwaita if available on target Ubuntu; fallback to plain `Gtk.ApplicationWindow` if not — flag which was used).
- Main window: sidebar (empty list "Instances") + a big "+ New Instance" button (`Adw.HeaderBar` action).
- **Test:** `python main.py` opens a blank window with the button. No crash.

### Ticket 1.2 — SQLite registry module (`core/registry.py`, `core/instance.py`)
- Implement schema from §1.2. Functions: `init_db()`, `create_instance(Instance) -> Result`, `list_instances() -> list[Instance]`, `get_instance(id) -> Instance|None`, `update_instance(id, **fields) -> Result`, `delete_instance(id) -> Result`.
- DB file lives at `~/.local/share/odoo-vite/odoo_vite.db`, created on first run.
- **Test:** `pytest tests/test_registry.py` — create, read, update, delete a dummy row, assert correctness. No GTK involved.

### Ticket 1.3 — System requirements checker (`core/system_check.py`)
- Function `check_requirements(odoo_version: str) -> Result` that:
  - Detects Ubuntu version (`/etc/os-release`).
  - Checks for: `python3`, `python3-venv`, `python3-pip`, `git`, `postgresql` (server + client), `libpq-dev`, `wkhtmltopdf`, `node`/`npm` + `rtlcss` (Odoo needs this for reports), build tools (`build-essential`, `libxml2-dev`, `libxslt1-dev`, `libjpeg-dev`, `libsasl2-dev`, `libldap2-dev`, `libssl-dev`, `zlib1g-dev`).
  - Maintain a small **version→extra-requirements map** as a Python dict (Odoo 13–14 need Python 3.7-3.8 range considerations, Odoo 17+ need Node ≥ 16, etc.) — start with a reasonable table for versions 15.0–18.0 and mark it `# TODO: PM to confirm exact matrix`.
  - Returns a structured report: `{ "missing": [...], "present": [...], "python_version_ok": bool, ... }`.
- Function `install_requirements(missing: list[str]) -> Result` that builds an `apt-get install -y <packages>` command and runs it via `pkexec` (GUI-friendly privilege escalation — **do not use raw `sudo` from a GUI app**, `pkexec` is the correct Linux desktop pattern) streaming output to the log events.
- **Test:** on a clean-ish Ubuntu VM, run `check_requirements("17.0")`, confirm it correctly flags at least one missing package, then `install_requirements(...)` on that package and re-check.

### Ticket 1.4 — Odoo branch fetcher (`core/git_manager.py`)
- Function `list_odoo_branches(search: str = "") -> Result` using `git ls-remote --heads https://github.com/odoo/odoo`, parse into version tags (filter to numeric-looking branches like `17.0`, `saas-17.1`, etc.), fuzzy-filter by `search` string.
- Cache result in memory for the session (avoid re-hitting network on every keystroke); debounce UI search input by ~300ms.
- **Test:** call function, assert `17.0` appears in the list when no search filter, assert filtering by `"16"` narrows results.

### Ticket 1.5 — "New Instance" wizard, Step 1 & 2 (UI only, no clone yet)
- `Adw.NavigationView`-based wizard (or `Gtk.Stack` if Adwaita unavailable) with steps:
  1. **Version picker:** search entry (calls `list_odoo_branches`) + list of matching branches, single-select.
  2. **System check:** on entering this step, call `check_requirements(version)` in a background thread, show a checklist UI (✅/❌ per dependency) with a "Fix automatically" button wired to `install_requirements`.
- No instance is created yet at this stage — this ticket is UI plumbing + wiring to Sprint 1's core modules only.
- **Test:** walk through steps 1–2 manually, confirm live branch search and live system check both work end-to-end with visible loading states (no UI freeze).

### Sprint 1 — Out of scope (explicitly, do not build yet)
- Actual `git clone` of the chosen version
- conf file generation, addons folder, enterprise picker
- start/stop/status
Flag to PM immediately if you find yourself needing any of these early — do not silently build ahead of scope.

### Sprint 1 report-back template (fill this in when done)
```
Ticket 1.1: [done/blocked] notes...
Ticket 1.2: [done/blocked] notes...
Ticket 1.3: [done/blocked] notes...
Ticket 1.4: [done/blocked] notes...
Ticket 1.5: [done/blocked] notes...
Deviations from architecture: ...
Open questions for PM: ...
```

---

## 4. Sprint 2 — Instance Creation Wizard (completion) — PREVIEW ONLY, detailed spec comes after Sprint 1 report

High-level scope so the dev can see what's next (do not start early):
- Wizard steps 3–6: instance name/port/db user/db password/db name form → validate port not in use, name unique.
- Clone community repo (`git_manager.clone`) with live log streaming into wizard.
- Create Python venv, `pip install -r requirements.txt` for the cloned version.
- Enterprise addons: optional "Browse folder" (`Gtk.FileDialog`) — if picked, validate it looks like an Odoo enterprise addons folder (contains modules with `__manifest__.py`).
- Custom addons folder: auto-create `<instance_path>/custom_addons` empty folder.
- `conf_writer.py`: generate `odoo.conf` with `[options]` section (addons_path assembled from community + enterprise? + custom, db_host, db_port, db_user, db_password, xmlrpc_port=<port>, logfile=<log_path>).
- `db_manager.py`: create Postgres role (db_user/db_password) if not exists, do NOT create the actual Odoo database yet — that happens on first start via `-i base` or via the Odoo db-manager UI (clarify with PM before assuming).
- Persist the new `Instance` row to registry only after every step succeeds (or use a transactional "draft" status so partial failures are cleanly resumable/removable).

---

## 5. Sprint 3 — Lifecycle Management (Start/Stop/Restart/Status) — PREVIEW ONLY

- `process_manager.py`: `start_instance(id, database=None)`, `stop_instance(id)` (graceful: SIGTERM, wait, SIGKILL fallback after timeout), `restart_instance(id, database=None)`, `get_statuses() -> list[dict]` batch call.
- Auto-update-on-start: read `auto_update_modules` from registry, append `-u mod1,mod2` to launch command when non-empty.
- UI: instance list rows show live status pill (running/stopped), PID, port, version, CPU%, mem — refreshed by polling `get_statuses()`.

---

## 6. Sprint 4 — Adopt / Remove / Switch DB / Track DB / Set Primary DB — PREVIEW ONLY

- **Adopt Instance:** point-and-import flow — user browses to an existing Odoo folder + conf file, app parses the conf, registers a row with `mode=adopted`, does NOT touch files.
- **Remove Instance:** managed → stop, delete `path` recursively (with a scary confirmation dialog + typed-name confirmation), optionally `DROP DATABASE`. Adopted → just delete the registry row, touch nothing on disk.
- **Switch Database / Set Primary Database:** stop → restart with `--database <new>` → update `primary_db` in registry.
- **Track Database:** list actual Postgres databases owned by `db_user` (`psql -U <user> -l` or `db_manager.list_databases(instance)`), let user manually add one to `tracked_dbs` JSON array without switching to it.

---

## 7. Open Questions for PM (developer: do not guess, ask)
1. Should `db_password` be stored via OS keyring (`libsecret`/`python-keyring`) or is plaintext SQLite acceptable for v1? (Recommendation: keyring — please confirm before Ticket 1.2 finalization if this changes the schema.)
2. Minimum supported Odoo version range for the requirements matrix (13.0? 15.0?) — confirm before finalizing Ticket 1.3's table.
3. Confirm `pkexec` is acceptable on target Ubuntu (GNOME) environments, or if we should prompt user to run a one-time `sudo apt install` themselves instead.

---

**Instructions to hand off:** Give the developer agent Sections 1–3 verbatim for Sprint 1. Sections 4–6 are context only ("here's where this is going") — do not let the agent start coding them yet. Have it fill in the report-back template at the bottom of Section 3 and return that to me when Sprint 1 is complete or blocked.
