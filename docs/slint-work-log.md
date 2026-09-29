# Slint Migration — Work Log (decision → PSS-4)

Covers everything done since the decision: **keep the Python backend
(`core/` untouched), replace the Qt frontend with Slint**. Qt app keeps
working throughout (`python3 main.py`); Slint runs beside it
(`python3 -m odoo_vite.ui_slint`). All Slint work below is uncommitted
working tree unless noted — last commit on main is `f303e76`
(Qt toolbar polish, pre-Slint).

Companion docs: `docs/slint-migration-plan.md` (PSS-1..9 roadmap),
`docs/slint-thread-safety.md` (beta abort: rules + incident record).

## Starting point

- Research + pros/cons of a C++/Slint remake → rejected full rewrite;
  chosen path: Python core + Slint frontend (Slint Python API).
- Codebase at decision: `core/` ~8k lines, `ui_qt/` ~7.5k lines,
  `tests/` ~7.2k lines, suite 332 green.
- Environment: Python 3.12.3, Ubuntu 24.04 X11. Slint installed from
  PyPI: **1.18.1b1** (newest available; Python API still beta),
  pinned exact in a new `pyproject.toml` `[slint]` extra so the Qt app
  stays 3.11-compatible. CI gained a `slint` job (3.12, `-k "slint"`).

## PSS-1 — Foundation (suite 332 → 337)

- New package `odoo_vite/ui_slint/` (`__init__.py`, `__main__.py`,
  `app.slint` shell, `bridge.py`): instance list from live
  `core/process_manager.get_statuses()`, 2s `slint.Timer` poll, select +
  refresh callbacks. No Qt imports anywhere in it.
- `tests/test_slint_foundation.py` (later merged into
  `tests/test_slint_bridge.py`): headless registry round-trip,
  selection callback, refresh picks up rows.
- `tests/test_no_slint_in_core.py`: fresh-interpreter guardrails — no
  Slint in `core/`, no Qt in `ui_slint/`.
- Live X11 proof: renders with zero platform-plugin issues (the Qt
  `libxcb-cursor0` saga doesn't exist in Slint's world).
- Beta facts verified against 1.18.1b1 (re-verify on every bump):
  callback args are **type-only, no names**; `Timer.start` takes a
  `datetime.timedelta`; property names must be snake_case
  (`current-id` parses as subtraction); `load_file` needs an abs path.

## PSS-2 — Shared components (suite → 348)

- `selection.py`: pure-logic `SelectionState` (grouping, filter,
  check-preservation by id, counts text, cursor) — Qt SelectionList
  contract ported, minus Qt.
- One component per file (beta discovery: the backend silently drops
  all but one non-Window component per file): `selection_list.slint`,
  `confirm_dialog.slint`, `typed_confirm_dialog.slint`
  (named `ok-enabled` gate rule), `toast_overlay.slint`,
  `progress_view.slint` (log cap + cancel Event). `Dialog` components
  are exempt and fully instantiable/headless-testable.
- `dialogs.py` drivers (compile-once cache): confirm/typed/clone
  dialogs, toast (coalesce + hide timer), progress (500-line cap).
- Struct conversion rejects unknown keys → `view_rows()` trims to
  struct shape. Layouts come in Box (std-widgets) vs Layout (builtin)
  flavors — don't mix.
- App sidebar rewired to the shared SelectionList.
- `tests/test_slint_selection.py` + `tests/test_slint_components.py`.

## PSS-3 — Overview + lifecycle (suite → 356)

- `overview.slint` extracted; reactive buttons bind `enabled` to
  `is-running` — zero Python gating code (the Qt stuck-off bug class
  has no equivalent).
- `lifecycle.py`: async ops over `asyncio.to_thread` with UI-thread
  sinks — replaces QThread/forwarder/BusyTracker with ~15 lines.
  Keyring resolves on the caller thread (Qt dbus rule, kept).
- Queue-drain threading: private loop thread + `run_coroutine_threadsafe`,
  results via queue drained by a 150ms Slint Timer (only UI-thread code
  touches the component).
- First-start `needs_confirm` surfaces as a **non-blocking** dialog
  instead of Qt's parked worker thread (genuinely nicer than Qt).
- Remove (adopted-plain / managed-typed), clone (name/port dialog +
  `SpinBox`), enterprise badge cached in bridge, toast overlay.
- `tests/test_slint_ops.py` groundwork (async ops over stubbed core).

## PSS-4 — Databases (suite → 386, green twice consecutively)

- `databases.slint`: switch combo (two-way), 4-column
  `StandardTableView` (nested ListModels, `current-row` two-way),
  ops buttons, schedules list. Table contract probed first
  (nested-model assign/read-back works).
- `databases.py`: `DatabaseOps` async + **byte-parity grouping** with
  the Qt implementation (tested against it), zenity file pickers
  behind a mockable seam.
- `discover_dialog.slint` / `schedule_dialog.slint` (presets +
  clobber-bug fix kept) / `files_dialog.slint` + drivers.
- Bridge tab wiring: set-primary/switch/track/discover/init/drop/
  backup/restore/validate + schedule CRUD/run/toggle/files; server
  banner with 20s TTL probe.
- Real bug found by the new tests: `sched_row or -1` made row 0
  unselectable — fixed in product code.
- Documented Qt divergences: restore target is the picked DB;
  confirms are non-blocking queue data.

## Thread-safety saga (the big one)

~30 controlled experiments into intermittent SIGABRTs
(`PyStruct is unsendable…`, SIGABRT under GC, moving sites).
Exonerated with evidence: shiboken hook (crash with zero Qt),
timers, two-way bindings, nested models, ComboBox/Table widgets,
keyring/dbus, subprocess-vs-sqlite, `run_event_loop` (hangs headless).
Remaining verdict: slint-python 1.18b1 race — Slint struct/model values
alive + worker threads allocating + GC. No upstream fix available.

Shipped containment (all genuine hardening, kept regardless):
weakref Slint-held callbacks, dialog own/release/dismiss discipline,
top-level imports (Qt PSQ-4 rule), in-place model sync
(`selection.sync_model`, incl. read-only-getter rule), quiescent
teardown (`wait_idle`, executor shutdown, timer stops), and the test
architecture that makes it deterministic: **Slint values and worker
threads never coexist in tests** — bridge tests stub all spawns
(record, never execute); ops tests run real core with zero Slint.
Full story + upstream draft: `docs/slint-thread-safety.md`.

## Current state

- `odoo_vite/ui_slint/`: 13 `.slint` views + `bridge.py`,
  `lifecycle.py`, `databases.py`, `modules.py`, `configuration.py`,
  `dialogs.py`, `selection.py`.
- `tests/test_slint_{bridge,ops,selection,components,databases,modules,
  configuration}.py` + `test_no_slint_in_core.py`: Slint suite.
- Suite: **514 passed, twice consecutively**; ruff clean; live X11
  proof re-run per sprint (empty log + timeout-kill = healthy loop).

## PSS-8 — Preferences + Export/Import (suite 503 → 514)

- `transfer.py`: `TransferOps` (export with caller-thread keyring
  resolve, import with blank-name guard, sync manifest preview) +
  pure `read_preferences`/`save_preferences`/`bundle_filename`.
  Bundle 600 perms are core's (already core-tested, not reduplicated).
- `preferences_dialog.slint` + `PreferencesDriver` (ComboBox instead
  of Qt radios, same choice + notes + keyring/registry status),
  `transfer_dialog.slint` + `ImportDriver` (name/port + scope note,
  blank-name guard). Shadowing caught pre-flight again (`_on_save`).
- Bridge: sidebar Preferences + Import… buttons (`sys-action`),
  Overview Export… button, zenity save/open picker kinds, preview →
  dialog → spawn flows. Unchanged mode closes silently (Qt parity).
- AboutSlint disclosure NOT added — conditional on the still-open
  royalty-free license choice (blocks PSS-9, not sprints).
- `tests/test_slint_transfer.py` (6) + 3 bridge flows + 2 driver
  tests. Live gate: mode flip persists; export → import round-trip on
  scratch data.

## PSS-7c — Scaffold wizard (suite 493 → 503)

- `wizards.py`: `scaffold_install` (db-exists pre-check first — never
  auto-creates — then generate + real install with progress/cancel) +
  pure `parse_field_lines`, `build_scaffold_definition`,
  `validate_scaffold` (PG check deferred to the worker, Qt parity).
- `scaffold_dialog.slint` + `ScaffoldDriver`: definition form
  (TextEdit field lines, instance combo, dest browse) → build log.
  Instances arrive as plain entries; completion reuses the shared
  wiz-done drain via a `prov_done` alias. Caught the shadowing class
  again pre-flight (`_on_browse_dest`).
- Bridge: Modules "New Module…" button (the PSS-5a omission),
  scaffold-dest picker kind, `_run_wiz_scaffold`. Live gate: form +
  validation + cancel paths (acceptance install is a user-side
  scratch-DB run).

## PSS-7b — Adopt wizard (suite 484 → 493)

- `wizards.py`: `adopt_run` op + pure `parse_adopt_paths` (live
  detected line), `validate_locate`, `gap_rows` (Qt 5-field parity),
  `suggest_db_name`, `build_adopt_overrides` (primary + filled gaps
  only + enterprise split — never invents values).
- `adopt_dialog.slint` + `AdoptDriver`: locate (live reparse on every
  keystroke + zenity browse) → gaps (dynamic rows, entries only for
  missing fields, slugified db default) → adopt run (status + Close).
  Same hook-attr discipline (browse hooks renamed before they could
  shadow — the fourth near-miss of the class).
- Bridge: sidebar Adopt… button, per-wizard WizardOps, adopt/conf/dir
  picker queue kinds, adopt-done drain (toast + refresh + select the
  adopted instance).
- `test_slint_wizards.py` +9 (real conf/community fixtures), 4 bridge
  adopt tests, AdoptDriver component tests.

## PSS-7a — Create wizard (suite 471 → 484)

- `wizards.py`: `WizardOps` (branches/syscheck/provision/discard) +
  pure `generate_password`, `normalize_branches` (Qt A.2 bare-list
  parity), `validate_details` (Qt gate parity, UI-thread-safe),
  `build_draft`/`refresh_draft` (Retry keeps id + path).
- `create_dialog.slint` + `CreateDriver`: version (shared list +
  refresh) → syscheck (advisory lines) → details (validated form +
  Generate) → provision (streamed log + Cancel/Retry/Discard/Close)
  page stack in one Dialog. Cancel leaves drafts in place (Qt parity).
- Bridge: sidebar New… button + `wiz-action`, per-wizard WizardOps
  with driver-bound queue sinks, `_run_wiz_provision` (progress
  pattern + cancel Event) and `_run_wiz_discard`; failed-step info
  preserved into the error toast.
- Real bugs found: `_on_discard` attr shadowed the method (third
  instance of the class — all drivers re-audited); `gen-password`
  wrote a wrong prop name; `viewport-y` deprecated → `content-y`
  (also fixed in the PSS-2 progress view).
- Test hygiene: draft-path tests redirect Path.home (the suite once
  created a real `~/.local` dir — removed).
- `tests/test_slint_wizards.py` (7) + 4 bridge wizard tests +
  CreateDriver component tests. Live gate for 7a: pages + validation
  + cancel paths (full provision is a user-side scratch run).

## PSS-6b — DevTools (suite 451 → 471)

- `devtools.py`: `DevToolsOps` (sessions + browser state, Qt flows
  parity) — rpc_connect/list_models/model_metadata/records_page/
  rec_search/rec_page/rec_create/rec_update/rec_delete/cron_refresh/
  launch_json/open_editor; pure `diff_record_values` (Qt parity),
  `editable_fields`, meta/record/cron formatters.
- `devtools.slint`: RPC connect (user/pass/remember), model picker
  (shared SelectionList), metadata line, domain row + paged record
  browser (shared list + New/Edit/Delete), cron list, launch.json +
  VS Code/Cursor buttons, PTY shell (start/stop/send + capped stream),
  module tests (module/db + confirm + shared progress), honest Dev Mode
  Watch placeholder (Slint has no QFileSystemWatcher — deferred).
- Record CRUD: metadata-driven `RecordDialog` + `RecordDriver`
  (LineEdits own text one-way, values round-trip); update goes through
  a diff-preview confirm; delete through the typed tier with the ir.*
  structural refusal kept. Real bug found: `_on_save` attr shadowed the
  method (same class as the AddonsDriver one — audited all drivers).
- Shell: bridge-owned sessions, 500ms UI-thread drain (Qt tick parity),
  start-race guard (`_shell_starting` — the tick must not reap a
  spawning session), quit-time stop, per-instance 1000-line caps.
- Qt bugs fixed in port: recall_credentials returns a TUPLE (Qt called
  .get() → Remember never worked); editable_fields checks ttype too
  (Qt's type-only check never skipped relationals).
- `tests/test_slint_devtools.py` (10, stubbed RPC boundary) + 10
  bridge tests (dispatch/paints/picks/record flows/shell/test-run) +
  RecordDriver component tests.

## PSS-6a — Logs (suite 435 → 451)

- `logs.py`: `LogOps` async (search/doctor/slow-refresh/profile) with
  typed sinks + kind-aware messages; pure `format_*` display helpers
  (Qt caps/prefixes parity) and `parse_profile_duration`.
- `logs.slint`: Follow toggle + Clear + Doctor + duration combo +
  Profile toolbar; search section; capped result lists; virtualized tail;
  slow-query section.
- Tail design: LogFollower owned by the bridge's 1s UI-thread Timer
  (file reads are cheap/sync — never on workers, the follower isn't
  thread-safe). Visibility gating via `TabWidget.current-index`
  two-way-bound to the bridge (LOGS_TAB) — the Qt lesson, kept exactly.
- Divergences: scroll-up auto-pause → explicit Follow toggle (ListView
  exposes no scroll position); tail cap 2000 not 5000 (display-only);
  `LOG_LEVELS` name collides with the conf levels (different contents!)
  — imported aliased on both sides after a test caught the mixup.
- Profile completions toast + auto-open the SVG via xdg-open.
- `tests/test_slint_logs.py` (10, real tmp-log search/doctor) + 7
  bridge tests (tail start/gating/rotation/missing/follow, dispatch,
  paints, stale guards).

## PSS-5 — Modules + Configuration (suite 386 → 435)

- `modules.py`: `ModuleOps` async (refresh/list+diff, install, inline -u
  update, uninstall, update_code, fetch_deps, run_tests) + pure
  `state_category` (Qt parity-tested), `preview_command` (exact argv for
  the confirm dialog — uses effective_python, Qt hardcoded venv python),
  `split_deps`. Real bug found: Qt's deps dialog reads graph["depends"],
  which core never returns — its panes were always empty; Slint splits
  core's edges (documented fix).
- `modules.slint`: search + state filter + checked multi-pick (picks in a
  bridge-side `_mod_checked` set, Qt parity — filtering through
  set_items([]) drops picks, found by test), Install/Update on checked,
  Uninstall/Deps on current row, Update-Code with guards. No Scaffold
  button (arrives with PSS-7 wizards).
- Progress plumbing (new pattern): bridge-owned ProgressDriver per op,
  worker lines post ("progress-line", (driver, line)) into the queue,
  drain appends on the UI thread; cancel via the driver's Event;
  `mod_busy` gates the view; Close button wired (`closed` callback +
  on_close hook — was dead since PSS-2).
- `configuration.py`: `read_conf_view` (local instant paint, Qt
  refresh_conf parity), `validate_meta`, `ConfigOps`
  (save/restore/regenerate/meta-save with workers pre-check first,
  apply-addons), `pick_open_dir` zenity seam.
- `configuration.slint`: conf table, 5 common editors, managed
  addons_path row, raw key/value, save/restore/regenerate, metadata
  (description/workers/log-level/python+browse), backup label.
- `addons_dialog.slint` + `AddonsDriver`: checklist + rename/up/down/
  remove + type-or-browse add + apply. Real bug found: `_on_apply`/
  `_on_browse` attrs shadowed same-named methods (view callbacks hit the
  raw hook — TypeError swallowed by Slint).
- UXS-1..4 (new `docs/slint-ui-ux-plan.md`, all done): clickable
  schedule rows (same zero-size TouchArea class as the sidebar — Edit/
  Run/Toggle/Delete were unreachable) + empty state; toast info/error
  kinds with kind-aware op sinks; db-busy gating; progress ScrollView;
  destructive ⚠ red confirm headings; Databases regrouped
  Inspect/Danger/Transfer; elide_middle ported; Overview pill + groups;
  sidebar filter; titled window; min sizes. Dark mode: system-follow
  verified by audit, nothing to enable.
- Test determinism (regressed to ~1/8 abort rate, fixed): dialog-result
  lambdas close over their driver while the driver holds the lambda — a
  cycle only cyclic GC frees, and CPython runs GC on any allocating
  thread incl. the idle loop thread (`__clear__` SIGABRT). Bridge tests
  now disable auto-GC with explicit main-thread collects at quiescence
  (3/3 green after; full suite 435×2). Rule added to
  `docs/slint-thread-safety.md`.

## PSS-9 — Cutover (in progress)

- License: royalty-free + AboutSlint disclosure (user decision).
  `about_dialog.slint` + `AboutDriver` (sidebar About Slint button);
  app version ("3.0.0") shown in About (was nowhere in Slint).
- Production GC hardening shipped: `gc.disable()` at entry,
  explicit owner-thread collects on dialog release + teardown
  (docs/slint-thread-safety.md updated from proposal to shipped).
- Conf-save honesty: every conf write toasts "(takes effect on next
  restart)" (Qt showed this; Slint didn't).
- Parity tests frozen as literals (ui_qt/ imports removed).
- Deleted: `odoo_vite/ui_qt/`, 15 `tests/test_qt_*.py`,
  `tests/test_no_pyside_in_core.py`.
- Packaging: PySide6 gone, `slint==1.18.1b1` promoted to a main
  dependency, `requires-python >=3.12`, version 3.0.0 (core/version.py
  in sync), pytest-qt dropped, CI single 3.12 job, launcher points at
  Slint without the xcb-cursor hack, README rewritten with the license
  note.
- Verification: `docs/slint-v2-checklist.md` (v2 re-run adapted with
  D1–D8 divergences) — owner's live pass gates the tag.

## Open items (not started)

- Owner live pass of `docs/slint-v2-checklist.md`, then tag `v3.0.0`
  (⚠️-only → tag, ❌ → one consolidated fix sprint).
- Upstream Slint issue (draft in `docs/slint-thread-safety.md`).
- Dev Mode Watch file-watch design (deferred since PSS-6b).
- Upstream Slint issue (draft in `docs/slint-thread-safety.md`).
