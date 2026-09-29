# Slint Frontend Migration Plan (PSS-1..N)

Decision: **Python `core/` stays untouched; `ui_qt/` is replaced by
`ui_slint/` (.slint markup + Python bridge).** Coexistence until cutover,
same playbook as the GTK→Qt migration (`docs/migration-retro.md` §1–8 —
foundation first, shared components before features, core/frontend split
commits, guardrails the day a bug class bites).

Run today: `python3 -m odoo_vite.ui_slint` (Slint) vs `python3 main.py`
(Qt). Install: `pip install --break-system-packages ".[dev,slint]"`.

## PSS-1 Foundation ✅ (this sprint)

- `odoo_vite/ui_slint/` (`app.slint` shell + `bridge.py` + `__main__.py`):
  instance list from live `core/process_manager.get_statuses()`, 2s
  `slint.Timer` poll, select + refresh callbacks. Live X11 proof: renders
  with zero platform-plugin issues (the Qt xcb-cursor class is gone).
- Verified-against-1.18.1b1 facts (re-verify on every bump — beta API):
  callback args are **type-only, no names**; `Timer.start` takes a
  `datetime.timedelta`; `.slint` property names must be snake_case
  (`current-id` parses as subtraction); `load_file` needs an abs path.
- Guardrails: `tests/test_no_slint_in_core.py` (no Slint in `core/`,
  no Qt in `ui_slint/`, fresh-interpreter probes).

## Threading decision (locked)

Slint owns the main thread; components must be touched there. Long ops go
through `slint.run_event_loop(main_coro)` + `asyncio.to_thread(core_fn)` —
results land back on the UI thread after `await`, which replaces
`ui_qt/workers.py` (QThread + forwarder + BusyTracker + teardown drain)
entirely. `Result(ok, message, data-dict)` maps 1:1 onto callback payloads
and `ListModel` rows. Timer stays for cheap polls (<2ms registry reads).

## Screen map (Qt → Slint)

| Qt today | Slint target | Notes |
|---|---|---|
| QMainWindow + toolbar + tabs | `AppWindow` + `TabWidget` | std-widgets |
| SelectionList (all pickers) | `StandardListView` + two-way model binds (1.17) | reactive: no reset/fingerprint code |
| Overview labels/buttons | properties + `Button`, state bindings | status pill via states |
| Databases tree (4 cols) | `StandardTableView` | ★ marker via cell text |
| QWizard (Create/Adopt/Scaffold) | `Wizard`? not std — custom page stack | biggest custom build |
| Logs tail/search/doctor | `ListView` + `LineEdit` + `ComboBox` | models, no landing-pattern hacks |
| ProgressDialog + cancel | custom dialog + `ProgressIndicator` | cancel Event as today |
| Menus/Preferences/Export | `MenuBar` + dialogs | tray optional (1.17+) |
| Dependency graph (d3) | **redesign**: Slint has no WebView — native list (already the fallback) | explicit scope cut |
| Toasts | custom popup, timer-driven | trivial |
| Theming | Slint dark-mode + palette natively | deletes the QSS layer |

## PSS-2..N (proposed order)

- **PSS-2 Shared components ✅ (this sprint)**: `selection_list.slint` +
  `selection.py` (filter/counts/checks/cursor contract, headless-tested),
  `confirm_dialog` / `typed_confirm_dialog` (named `ok-enabled` gate rule),
  `toast_overlay`, `progress_view` (log cap + cancel Event drivers in
  `dialogs.py`), app sidebar rewired to the shared list. Beta findings:
  one non-Window component per file (restructure to Dialogs or split
  files); `Dialog` components fully exempt; struct conversion rejects
  unknown keys (bridge via `view_rows()`); layouts come in Box
  (std-widgets) vs Layout (builtin) flavors — don't mix.
- **PSS-3 Sidebar + Overview + lifecycle ✅ (this sprint)**: reactive
  Overview (buttons bind `enabled` to `is-running` — zero Python gating),
  enterprise badge cached in bridge, toast overlay, `lifecycle.py` async
  ops (`asyncio.to_thread`, queue-drain delivery — deletes the QThread /
  forwarder / BusyTracker layer), remove (adopted-confirm / managed-typed)
  + clone (name/port dialog) flows, first-start `needs_confirm` surfaced
  as a non-blocking dialog instead of Qt's parked worker thread.
- **PSS-4 Databases ✅ (this sprint)**: `databases.slint` (switch combo,
  columned table, ops, schedules), `DatabaseOps` async + Qt grouping
  parity (tested against the Qt implementation), discover/schedule/files
  dialogs + drivers, zenity file pickers (mockable seam, manual path when
  missing), bridge tab wiring (set-primary/switch/track/discover/init/
  drop/backup/restore/validate + schedule CRUD/run/toggle/files).
  Divergences from Qt (documented choice): restore target is the picked
  DB; first-start/switch confirms are non-blocking queue data. Testing
  invariant: Slint values and worker threads never coexist in tests
  (spawns stubbed/recorded in bridge tests; real core in Slint-free ops
  tests) — enforced after a long abort hunt, full story in
  `docs/slint-thread-safety.md`. Threading
  rules + incident record: `docs/slint-thread-safety.md` (beta abort
  contained by test architecture, not luck).
- **PSS-5 Modules + Configuration** (tables, conf editors, addon manager).
- **PSS-6 Logs + DevTools** (tail, search, RPC browser, shell — shell keeps
  the PTY core, renders streamed text).
- **PSS-7 Wizards** (create/adopt/scaffold as a page stack).
- **PSS-8 Preferences + Export/Import + Preferences parity.**
- **PSS-9 Cutover ✅ (in progress, this doc closed out by it)**:
  delete `ui_qt/`, PySide6 → optional, docs sweep,
  `v3.0.0` tag after the v2 checklist re-run on Slint.

## Open decisions (must close before any release)

1. **License.** The repo has no LICENSE file and Slint is triple-licensed
   (GPLv3 / royalty-free-with-disclosure / commercial). Shipping Slint
   requires picking one: GPLv3 = add LICENSE (free, no strings for open
   source); proprietary without GPL = royalty-free + `AboutSlint`
   disclosure widget, else a paid seat plan. PySide6's LGPL asked nothing.
2. **Python floor.** `slint` needs 3.12+; the Qt app stays 3.11-compatible.
   Kept as an opt-in extra for now — cutover bumps `requires-python`.
3. **Beta drift.** `slint` is beta: pin exact, re-run the Slint suite +
   the three 1.18 syntax facts on every bump (CI `slint` job does this).
