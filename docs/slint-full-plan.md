# Slint Migration — Full Plan (PSS-1 → cutover)

Parent docs: `docs/slint-migration-plan.md` (roadmap sketch),
`docs/slint-work-log.md` (what's done), `docs/slint-thread-safety.md`
(beta rules + incident record). This file is the buildable plan: scope,
acceptance, order, and exit criteria per sprint.

## 1. Goal and non-goals

**Goal:** replace `ui_qt/` (PySide6) with `ui_slint/` (.slint markup +
Python bridge) with zero behavior regressions, keeping `core/`
untouched. Coexistence until cutover: both frontends run from the same
checkout (`python3 main.py` vs `python3 -m odoo_vite.ui_slint`).

**Non-goals:** no core rewrites (bugs found in core ship as separate
core commits, per migration-retro §4); no new product features
mid-migration (clone/export/Preferences parity only — what exists in
Qt must exist in Slint, nothing more); no WebView replacement (the
dependency graph ships as the native list, already the Qt fallback);
no `run_event_loop` adoption (hangs headless — queue-drain stays).

## 2. Ground rules (violations send the sprint back)

1. `core/` imports neither Qt nor Slint (guardrail
   `tests/test_no_slint_in_core.py`); `ui_slint/` imports never Qt.
2. Main thread owns every Slint touch. Workers run core only, results
   return through the queue drained on the UI thread.
3. Slint-held callbacks are weak (`_wcb`/`_dcb`); dialogs are owned +
   dismissed + released (`_own`/`_release`/`dismiss()`).
4. Models sync in place (`selection.sync_model`); never mutate a
   `view.rows` getter handle (read-only wrapper).
5. **Tests:** Slint values and worker threads never coexist — bridge
   tests stub all spawns (record, never execute); ops tests run real
   core with zero Slint values. Live X11 run is the UI integration
   gate, re-run per sprint (empty log + timeout-kill = healthy).
6. One non-Window component per `.slint` file (beta codegen rule);
   dialogs inherit `Dialog`.
7. Every sprint ends: ruff clean, full suite green twice, live proof,
   plan checkboxes ticked, core/frontend split commits.

## 3. Widget mapping (Qt → Slint, status)

| Qt today | Slint target | Status |
|---|---|---|
| QMainWindow + toolbar + QTabWidget | `AppWindow` + `TabWidget` | ✅ shell done |
| SelectionList (all pickers) | `SelectionList` + `SelectionState` | ✅ PSS-2 |
| ask_confirm / typed-confirm | `ConfirmDialog` / `TypedConfirmDialog` (`ok-enabled`) | ✅ PSS-2 |
| Toaster | `ToastOverlay` + coalescing driver | ✅ PSS-2 |
| ProgressDialog + cancel | `ProgressView` + cancel Event driver | ✅ PSS-2 |
| Overview page | `OverviewView`, reactive `enabled` | ✅ PSS-3 |
| Start/Stop/Restart/Remove/Clone | `LifecycleOps` + remove/clone dialogs | ✅ PSS-3 |
| Databases page + tree | `DatabasesView` + `StandardTableView` | ✅ PSS-4 |
| Switch/track/discover/schedules/files | `DatabaseOps` + 3 picker dialogs + zenity seam | ✅ PSS-4 |
| Modules tab (table + install/update) | `ModulesView` + `ModuleOps` | PSS-5 |
| Configuration tab + addon manager | `ConfigurationView`, conf editors | PSS-5 |
| Logs tail/search/doctor/slow/profile | `LogsView`, visibility-gated tail | PSS-6 |
| DevTools RPC/models/records/cron | `DevToolsView` record browser | PSS-6 |
| Odoo shell PTY | streamed text view (core PTY stays) | PSS-6 |
| Create/Adopt/Scaffold wizards | page-stack + existing drivers | PSS-7 |
| Preferences dialog | `PreferencesDialog` + settings | PSS-8 |
| Export/Import | `transfer.py` reuse + pickers | PSS-8 |
| Dependency graph (d3) | native list fallback | scope cut, keep fallback |
| QSS light/dark | Slint palette/dark-mode native | PSS-9 sweep |

## 4. Sprint plans

### PSS-5 — Modules + Configuration
- **Scope:** `modules.slint` (table via nested models, search + state
  filter, checked-set persistence in Python like Qt's `_checked`),
  `ModuleOps` async (list/install/update/uninstall/update-code/deps/
  tests — all accept `progress_cb`+`cancel`, reuse `ProgressDriver`),
  `configuration.slint` (conf table, common-key editors, raw key/value,
  save/restore/regenerate, metadata editors, addon manager dialog
  reusing shared SelectionList).
- **Accept:** install/update/uninstall round-trip on a throwaway DB;
  conf edit → backup → restore reverts; addon Add/Edit/Enable/Remove/
  Reorder round-trips into the real conf file; progress cancel works.
- **Risks:** module list scale (1500+ rows — virtualized `ListView`
  required, scale-test like Qt REG.3); pip streaming duration.
- **Tests:** headless drivers + stubbed bridge dispatch + real-core
  ops tests (Slint-free), mirror PSS-4 structure.

### PSS-6 — Logs + DevTools
- **Scope:** `logs.slint` (tail view with follow/pause, search results,
  doctor findings, slow queries, profiler trigger), `devtools.slint`
  (RPC connect, model picker via shared list, paged record browser,
  cron list, launch.json + editor open, shell view, tests runner).
  Tail polling gated on tab visibility (Qt lesson, kept).
- **Accept:** follow follows + scroll-up pauses; search/doctor render;
  record CRUD on throwaway data with diff preview; shell echoes a
  read-only command; tests refuse primary DB.
- **Risks:** shell PTY output volume (cap + auto-scroll like Qt);
  long test runs need cancel (ProgressDriver already supports it).

### PSS-7 — Wizards
- **Scope:** page-stack component (validate-per-page, WorkerPage
  equivalent: log view + Cancel/Retry/Discard), Create (version →
  syscheck → details → provision streaming), Adopt (locate → gaps →
  run), Scaffold (definition → build). Reuse drivers + ops patterns;
  no new threading inventions.
- **Accept:** full create on a scratch instance; adopt gap-fill;
  scaffold + real install; cancel kills subprocess, Retry resumes,
  Discard cleans (draft + keyring + row).
- **Risks:** longest sprint (3 wizards); provision streaming needs a
  progress-capable driver (extend `ProgressDriver`, don't fork it).

### PSS-8 — Preferences + Export/Import parity
- **Scope:** `preferences_dialog.slint` (provisioning mode radio,
  keyring status, registry path) wired to `core/settings`;
  File-menu Export/Import (bundle pickers + `transfer.py` reuse +
  honest scope notes); AboutSlint disclosure widget **iff**
  royalty-free license is chosen (§6.1).
- **Accept:** mode flip persists across restart; export → import
  round-trip on scratch data; bundle permission 600.

### PSS-9 — Cutover
- Delete `ui_qt/` (single directory removal, like GTK before it).
- PySide6 → optional dependency; `requires-python` → `>=3.12`;
  README install/run rewritten; desktop launcher points at Slint.
- Docs sweep: design-system/patterns/retro references, README,
 -install notes, `slint-work-log.md` closed out.
- Re-run the v2 verification checklist against Slint end-to-end on
  real Ubuntu + Postgres; tag `v3.0.0` on clean (⚠️-only → tag,
  ❌ → one consolidated fix sprint, same rule as v2).

## 5. Cutover exit criteria (all must hold)

1. `pytest tests/ -q` green twice + ruff clean + live X11 proof.
2. Every Qt screen has a Slint counterpart or a documented cut (§3).
3. v2 checklist re-run on Slint: no ❌.
4. License chosen + attribution in place (§6.1).
5. No `ui_qt`/`PySide6` imports outside deleted code
   (`grep -r PySide6 odoo_vite/ tests/` empty).

## 6. Open decisions

1. **License (blocks release, not sprints).** No LICENSE file exists.
   Options: (a) GPLv3 — add LICENSE, free, no strings for open
   source; (b) royalty-free — free for proprietary desktop but
   requires `AboutSlint` disclosure (PSS-8 scope); (c) commercial —
   paid seats. PySide6's LGPL asked nothing; decide before PSS-9.
2. **Production GC hardening.** `gc.disable()` + quiescent-collect
   passed stress 12/12 but is unproven in live sessions — gate behind
   soak testing with real usage, never behind the suite. Default:
   ship without it unless soak proves need.
3. **Upstream Slint issue.** Draft in `docs/slint-thread-safety.md` —
   file it with the bisect log once PSS-5 starts (more lived evidence
   by then); track the binding release notes for a Send/GC fix and
   re-probe the quarantine on every bump.

## 7. Ordering rationale + effort

Foundation → components → screens in dependency order (each sprint's
screens consume only prior sprints' components); threading/ownership
rules were paid for once in PSS-1..4 and never re-decided. Rough
sizes: PSS-5 large (2 tabs + flows), PSS-6 large (RPC browser + shell),
PSS-7 largest (3 wizards), PSS-8 small, PSS-9 checklist. Qt history
suggests ~1 sprint per tab group at this team's pace with the
per-sprint gates in §2.
