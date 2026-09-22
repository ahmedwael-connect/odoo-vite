# Odoo Vite — Sprint 2 Spec: Instance Creation Wizard (Completion)

**Depends on:** Sprint 1 (accepted). Uses `core/registry.py`, `core/instance.py`, `core/git_manager.py`, `core/result.py`, `core/events.py`, and the wizard shell from Ticket 1.5.

**Answers carried over from Sprint 1 Q&A (bake these in):**
- `db_password` storage: keyring primary, SQLite plaintext fallback, tracked via new `password_storage` column (`"keyring"`|`"plaintext"`) on `instances`. Add this column now via a small migration function in `registry.py` (`ensure_schema()` — additive `ALTER TABLE` guarded by a `try/except` checking `sqlite3.OperationalError: duplicate column`, or a `schema_version` table if you prefer a cleaner migration pattern — your call, note which you pick).
- Min Odoo version: 15.0.
- `pkexec` approved, keep as-is.

---

## Goal of this sprint
Walking out of the wizard, a **fully working, first-run Odoo instance** exists on disk: cloned, venv'd, configured, registered in SQLite — ready for Sprint 3 to press "Start" on it.

---

## 1. Data model addition
Add to `instances` table (via `ensure_schema()`):
- `password_storage TEXT` (`"keyring"` or `"plaintext"`)
- `status` should default to `"draft"` until Sprint 2 finishes successfully, then `"stopped"`. A `"draft"` instance that never completes must be cleanly removable/resumable — do not leave half-built folders with no registry trace, and do not leave registry rows pointing at folders that don't exist. Prefer: create the registry row **first** as `draft`, then do file operations, then flip to `stopped` on success. On failure, leave it `draft` with a `last_error` (new nullable TEXT column) so the wizard can offer "Resume" or "Discard" if the user reopens it.

## 2. Wizard steps 3–6 (UI)

### Step 3 — Instance details form
Fields:
- **Instance name** (text, required, unique — validate live against `registry.list_instances()` names, debounce ~300ms, show inline error)
- **Port** (number, default suggestion = next free port starting at 8069, scanning existing registry rows' ports + doing a real `socket.bind` test to catch ports used by non-Odoo-Vite processes too)
- **DB user** (text, default `odoo`)
- **DB password** (password entry, default `odoo`, with a "generate strong password" button — nice-to-have, not blocking)
- **DB name** (text, default = slugified instance name, e.g. `Odoo Client A` → `odoo_client_a`)

Validate all fields before allowing "Next": name unique + non-empty, port free + in valid range (1024–65535), db name matches `^[a-z_][a-z0-9_]*$` (Postgres identifier safety — reject/auto-fix otherwise).

### Step 4 — Enterprise addons (optional)
- "Do you have Odoo Enterprise addons?" toggle.
- If yes: `Gtk.FileDialog` folder picker. On selection, validate: folder contains at least one subfolder with a `__manifest__.py` file. If invalid, show inline warning but allow proceeding (don't hard-block — user may know better than our heuristic), just flag clearly.
- If no: skip, `enterprise_path` stays `NULL`.

### Step 5 — Review & confirm
- Read-only summary of everything chosen across steps 1–4 (version, name, port, db user, db name, enterprise path if any).
- "Create Instance" button triggers Section 3 below in a background thread.

### Step 6 — Provisioning progress
- Live log pane (reuse the pattern from Ticket 1.3's install log streaming) showing: clone progress → venv creation → pip install → conf write → db role creation.
- Progress must be cancellable up to (but not during) the actual `git clone`/`pip install` subprocess calls — cancelling should kill the subprocess (`Popen.terminate()`) and mark the draft row for cleanup, not leave an orphaned process.
- On success: "Open Instance" button → navigates to the (stub, Sprint 3 will fill it in) instance detail page.
- On failure: show the error, keep the `draft` row, offer "Retry" (re-run from the failed step, not from scratch) and "Discard" (delete partial files + registry row).

## 3. Core logic (`core/` — no GTK)

### 3.1 `git_manager.clone_instance(version: str, dest_path: str, progress_cb) -> Result`
- `git clone --branch <version> --depth 1 https://github.com/odoo/odoo <dest_path>/community`
- Stream stdout/stderr line-by-line to `progress_cb(line: str)`. Run via `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True)` and iterate `proc.stdout`.
- Return `Result(ok, message, data={"community_path": ...})`.

### 3.2 New module `core/venv_manager.py`
- `create_venv(instance_path: str, progress_cb) -> Result` — `python3 -m venv <instance_path>/venv`
- `install_requirements(venv_path: str, community_path: str, progress_cb) -> Result` — `<venv>/bin/pip install --upgrade pip`, then `<venv>/bin/pip install -r <community_path>/requirements.txt`, streamed the same way as clone. Note: some Odoo versions' `requirements.txt` fail on certain system lib versions (e.g. `psycopg2` needing `libpq-dev`, already checked in Sprint 1) — if pip fails, surface the **last ~20 lines** of output in the `Result.message` so the wizard can show something actionable, not just "failed."

### 3.3 `core/conf_writer.py`
- `write_conf(instance: Instance) -> Result`
- Builds `addons_path` = comma-joined list of: `<community_path>/addons`, `<enterprise_path>` (if set), `<custom_addons_path>`.
- Writes `<instance_path>/odoo.conf`:
  ```ini
  [options]
  addons_path = <computed>
  db_host = localhost
  db_port = 5432
  db_user = <db_user>
  db_password = <db_password>
  xmlrpc_port = <port>
  logfile = <log_path>
  ```
- Also creates `<instance_path>/custom_addons/` (empty dir) and `<instance_path>/logs/odoo.log` (empty file, ensure parent dir exists) before writing the conf, since `logfile` must point somewhere valid.
- Return `Result` with `conf_path`, `log_path`, `custom_addons_path` in `data`.

### 3.4 `core/db_manager.py` (start of this module — Sprint 4 will extend it)
- `ensure_role(db_user: str, db_password: str) -> Result` — runs `psql -U postgres -c "..."` (or uses `subprocess` + `createuser`/`psql` — pick whichever is more robust to a fresh Ubuntu Postgres install; note in your report which approach you used and why) to create the role if it doesn't already exist, `CREATEDB` privilege granted (Odoo needs to create/drop its own working databases in some flows — confirm this is acceptable or flag to PM if you think least-privilege should be tighter).
- Do **NOT** create the actual Odoo `db_name` database yet in this sprint — first real DB creation happens on first `Start` (Sprint 3), typically via `odoo-bin -i base -d <db_name> -c <conf_path>` or Odoo's own db-manager. Just get the role ready.
- Return `Result`.

### 3.5 Orchestration — `core/provisioning.py` (new)
- `provision_instance(instance: Instance, progress_cb) -> Result` — the single function the wizard's Step 6 calls in a background thread. Runs, in order, with early-exit on first failure:
  1. `registry.create_instance(instance)` with `status="draft"`
  2. `git_manager.clone_instance(...)`
  3. `venv_manager.create_venv(...)`
  4. `venv_manager.install_requirements(...)`
  5. `conf_writer.write_conf(...)`
  6. `db_manager.ensure_role(...)`
  7. `registry.update_instance(id, status="stopped")`
- On any step failing: `registry.update_instance(id, status="draft", last_error=<message>)`, return the failing `Result` up to the UI.
- Design this so **Retry** (from the wizard) can re-enter at the failed step rather than redoing everything — e.g. check `os.path.exists(community_path)` before re-cloning, check venv exists before recreating, etc. Idempotency here matters more than elegance; a simple `if already done: skip` per step is fine.

---

## 4. Testing expectations
- `pytest` coverage for `conf_writer.write_conf()` (assert file contents match expected ini structure — no GTK, no network needed, fully unit-testable) and for `provisioning.py`'s retry/idempotency logic using mocked/stubbed sub-calls (don't hit real network/postgres in unit tests — mock `git_manager`/`venv_manager`/`db_manager` calls and assert orchestration order + failure handling).
- One manual end-to-end pass on real Ubuntu: pick 17.0, fill the form, no enterprise, create → confirm `odoo.conf`, `custom_addons/`, `logs/odoo.log`, venv, and cloned community folder all exist and conf content is correct. Attach screenshot/log of this run in the report.

## 5. Out of scope for Sprint 2 (do not build)
- Actually starting/stopping the odoo-bin process (Sprint 3)
- Creating the real Odoo database / running `-i base` (Sprint 3, on first Start)
- Adopt/Remove/Switch DB/Track DB (Sprint 4)

## 6. Report-back template
```
Ticket 2.1 (schema migration + password_storage): [done/blocked] notes...
Ticket 2.2 (wizard steps 3-6 UI): [done/blocked] notes...
Ticket 2.3 (git_manager.clone_instance): [done/blocked] notes...
Ticket 2.4 (venv_manager): [done/blocked] notes...
Ticket 2.5 (conf_writer): [done/blocked] notes...
Ticket 2.6 (db_manager.ensure_role): [done/blocked] notes...
Ticket 2.7 (provisioning orchestration + retry/idempotency): [done/blocked] notes...
Manual E2E run: [pass/fail] + what was verified
Deviations from architecture: ...
Open questions for PM: ...
```
