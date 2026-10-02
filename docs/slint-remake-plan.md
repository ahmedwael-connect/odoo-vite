# Slint Remake Plan (RM-0 → RM-7) — architecture-first, same Material look

Verdict from the 2026-10-01 state analysis: incremental UX sprints
(UXB-0..5) hit diminishing returns because the *architecture* is the
bottleneck, not the pixels. This plan rebuilds the system underneath
the current look.

Owner decisions (locked):

1. **Scope** — architecture + design system first; the Material look
   is preserved (no visual redesign, no re-brand).
2. **Execution** — staged strangler: every sprint ends suite-green +
   ruff + relaunch + screenshots at 3 widths. Old code is deleted only
   after its replacement passes. No big-bang branch.
3. **Style** — full Material, **dark + light** token sets (closes
   ML-2's pending A/B: Material wins; the pin in `main.py` stays).

## What the state analysis found (the mandate)

| Finding | Evidence | Cost |
|---|---|---|
| 27 theme tokens, 3 referenced; 19/26 files never import Theme | 7 files use `Theme.*`; 70 hardcoded `gray/red/font-size` hits | design system exists on paper only |
| `AppWindow` fat pass-through shell | app.slint:29-170 → 108 `in-out` props + 28 callbacks | every new view = ~10 props + 4 callbacks |
| 112 hand-rolled buttons, 48 enable-gates, 18 empty-state lines, 12 dialog footers, 3 wizard footers, 8 raw line-lists | all inline, no components | duplication is the bug generator |
| Two giant string dispatches | `_drain` 50 branches (bridge.py:479-568); ~93 `action ==` branches across 9 handlers | no registry, no type-check, unbounded growth |
| God object | one `SlintBridge` class, bridge.py:175-2371, ~60 fields, 200-line `__init__` | every domain edit touches one file |
| **Live bug: bridge-owned Cancel is a no-op** | bridge.py:706 overwrites `dialogs.py:67-68`'s `_finish(False)` wiring; tests bypass it | confirm dialogs' cancel path dead |
| Blocking I/O on UI thread | `os.write` shell send (bridge.py:1731); file read every 1s (`_tail_tick`, bridge.py:2011) | jank |
| Zero a11y/keyboard | 2 `accessible-*` props total; no Esc/arrow/focus anywhere | mouse-only app |
| Structural lie | databases.slint:105-176 ("Inspect"/"Danger zone"/"Transfer") nested *inside* the "Tracked databases" GroupBox | indentation lies about structure |
| Tests see happy paths only | 74 bridge tests with stubbed coroutines | queue races + cancel bug invisible |

Docs debt: `design-system.md` / `patterns.md` are still Qt/GTK-worded.

## Probe results (1.18.1b1 — verified this session, never assumed)

| Construct | Result | Unlocks |
|---|---|---|
| `global G { callback ping(int); }` + `G.ping(1)` | **OK** | callbacks can leave `AppWindow` (RM-3) |
| `global G { in-out property<M> meta; }` (struct-typed) | **OK** (needs `in-out`) | per-view state structs in a store |
| `struct M { a: string, b: int }` + `in-out property<M>` | **OK** | grouped state instead of 108 scalars |
| `FocusScope { key-pressed(event) => … Key.Escape … }` | **OK** (`Window` itself rejects `key-pressed`) | RM-6 Esc/arrow nav |
| `if self.width < 800px: Element { }` | **OK** | RM-7 breakpoints |
| Property syntax in this codebase | `in-out property<T> name: v;` (the `<name>: T` shorthand fails to parse here) | use existing style |

## Ground rules (carried forward — non-negotiable)

- `core/` never imports Slint; main thread owns every Slint touch.
- Weak callbacks (`_wcb`/`_dcb`); dialogs owned and released on the
  owner thread; `gc.disable()` at entry + owner-thread `gc.collect()`
  on release (slint-python PyStruct race containment — do not "clean
  this up").
- Never name a Python attribute the same as a method (recurring
  shadowing class: `_on_save`, `_db_sort`, `_discard_cb` renames).
- `Result(ok, msg, data)` never raises into the UI; diff-before-repaint
  on every poll; max-width + elide on every long value.
- No pixels, no pass: screenshots at 800/1100/1400 after every sprint.
- Probes before promises: anything not in the probe table gets a
  `/tmp/probe` compile check before it enters a sprint.

## Sprints

### RM-0 — Guardrails (make the remake safe to run fast)

- `tests/test_no_slint_styles.py`: regex-scan all `ui_slint/*.slint`
  for banned style literals (`color: gray|red|orange|green|white`,
  `font-size: Npx`, `font-weight: 700`, raw hex) **outside
  `theme.slint`**, checked against a checked-in
  `tests/style_allowlist.txt`. New violations fail immediately;
  later sprints only shrink the allowlist.
- Duplication counters (same file or companion test): counts of
  `colorize-icon`, `enabled: has-instance`, empty-state pattern —
  recorded now, asserted non-growth now, asserted shrinkage in RM-2.
- Baseline screenshot set (dark, 800/1100/1400, all 6 tabs) checked
  into the work-log as the "keep the look" reference.
- **Accept:** three enforcement tests green with today's numbers;
  baseline set attached; zero product-code changes.

### RM-1 — Token truth (theme.slint becomes the only style source)

- Expand `theme.slint`: complete type ramp actually used (headline,
  title, body, label, caption, mono), spacing scale applied (4/8/12/
  16/24/32), semantic roles (`dim`, `danger`, `warn`, `ok`, `accent`,
  `snack-bg`, `err-light`), radius, `form-label-w`, mono family.
- **Dark + light token sets**: one `in-out property<bool> dark-mode`
  on the Theme global switches every role brush; dark set == today's
  values (look preserved byte-for-byte). Light set defined here,
  visually QA'd in RM-7.
- Sweep all 70 hardcoded style hits → Theme refs; all 16 dialogs
  import Theme; kill literal duplicates (`#323232`/`#ffb4ab` at
  app.slint:451,457).
- Danger/destructive role replaces the hand-placed `"⚠ "` string
  prefixes (app.slint:456, confirm:25, typed_confirm:23, databases:45).
- Empty the allowlist in RM-0's test to zero.
- **Accept:** enforcement tests green with empty allowlist; dark
  screenshots pixel-diff against baseline (chip drift allowed only
  where a role was mis-mapped); light set renders everywhere.

### RM-2 — Component library (kill the duplication)

New `ui_slint/components.slint` (or a folder — decide at sprint start):

- `ActionButton` — icon + optional label + `enabled` gate + Tooltip +
  pressed state; absorbs the 112 buttons and 48 `has-instance &&
  !busy` gates (gate becomes one boolean input: `primary && !busy`).
- `EmptyState` / `NoticeText` — absorbs the 18 gray empty-state lines
  and ad-hoc dim Texts.
- `SectionHeader` + `PageHeader` — absorbs the inline 14px/700 headers.
- `StatusPill`, `ErrorBanner` (absorbs databases.slint:37-50 inline
  banner + border hack), `Snackbar` — **one toast implementation**;
  delete dead `toast_overlay.slint` + `ToastDriver` (dialogs.py:176)
  or promote one of them as the single source.
- `LineList` / `LogView` — elide + optional mono + auto-scroll
  (`content-y: max(0, h - visible-h)` computed once); absorbs the 8
  raw lists + 3 auto-scroll copies.
- `FormField` — labelled GridBox row; absorbs the form label rows
  (configuration/create/scaffold/adopt) and kills the 110/111/140/150px
  label-width hacks in favor of `Theme.form-label-w`.
- `DataTable` wrapper around the one `StandardTableView` use.
- Migrate all six views + dialogs onto the components; refresh
  RM-0's duplication counters to the new asserted ceilings.
- **Accept:** counters at agreed ceilings (target: inline
  `colorize-icon`/empty-state/gate counts ≈ 0 outside
  `components.slint`); suite green; screenshots unchanged.

### RM-3 — Shell remodel (108 props → a store)

- New `ui_slint/store.slint`: `global AppStore` holding nav
  (`tab-index`, sidebar filter/selection), status/snackbar, and
  **per-view state structs** (`OverviewState`, `DbState`, …) — all
  probe-verified constructs.
- Move the ~28 callbacks into domain globals (`db-action(string)`,
  `mod-action(string)`, …): views call them directly; `app.slint`
  stops forwarding.
- `app.slint` shrinks to shell layout + view mounting + store
  bindings; enforce a ceiling (e.g. ≤ 30 property declarations) in a
  test so it can't regrow.
- Fix the databases.slint:105-176 nesting lie (promote Inspect/Danger/
  Transfer out of the GroupBox) + repo-wide indentation sweep of the
  copy-paste damage (app.slint:317-321, logs.slint:38-69,
  devtools.slint:257-266, configuration.slint:143-204).
- Keep all 6 tabs statically mounted (Slint has no lazy mount —
  recorded limit, don't fight it).
- **Accept:** prop-declaration ceiling test green; bridge.py changes
  are mechanical (property paths only) and suite stays green;
  screenshots identical to baseline.

### RM-4 — bridge.py decomposition (strangler inside one file)

Order matters — bug first, then structure:

- **RM-4a: fix the `_own` cancel clobber** (bridge.py:706). Contract:
  drivers own `view.cancelled`; the bridge attaches through one
  documented hook (e.g. driver `set_close_hook(cb)` or a dedicated
  `view.closing` callback). Confirm/typed-confirm `_finish(False)`
  fires when bridge-owned. Regression tests covering cancel for
  every bridge-owned dialog family.
- **RM-4b: registries over if/elif** — `self._drainers:
  dict[str, Callable]` for the 50-branch `_drain`; per-view
  `_actions: dict[str, Callable]` for the ~93 action branches.
  Equivalence: existing tests must pass unchanged.
- **RM-4c: dedup + delete** — one `_instance(kind)` resolver (kills
  bridge.py:675/1086/1446 + 2 inlined copies), shared `_run` helper
  for the 5 Ops modules, one `_post_*` builder; delete dead code
  (duplicate `_PREIMPORTED_FOR_WORKERS` bridge.py:61-76, unused
  `ToastDriver`, test-only `gate_ok` moved into tests).
- **RM-4d: UI-thread hygiene** — `_shell_send_flow` `os.write` and
  `_tail_tick` file reads hop to a worker; timers only marshal cached
  results. Keep the gc containment exactly as is.
- **RM-4e: controller split** — extract per-domain sections into
  `ui_slint/controllers/{databases,modules,configuration,devtools,
  logs,wizards}.py`, each a class receiving a narrow `UiPort`
  (prop write + post + spawn). `SlintBridge` remains composition
  root + queue pump. Move tests section-by-section; leave thin
  delegating shims only where tests legitimately need them, and
  remove shims as tests migrate.
- **Accept:** suite green after each sub-step (especially 4a with new
  cancel tests and 4b with dispatch equivalence); ruff; live smoke:
  start/stop instance, cancel a confirm, tail a log — no jank.

### RM-5 — Dialog & wizard system (17 drivers → 1 framework)

- `DialogDriver[T]` base: template `show()/dismiss()` (today copy-
  pasted ×17 at dialogs.py:74-82, 109-117, …), one `close` contract
  (from RM-4a), one place for Esc-to-close (consumes RM-6 infra).
- Driver registry: kind → class; bridge's 3 near-identical wizard
  openers (bridge.py:1813-1845, 1924-1979) collapse to 1 generic
  opener.
- `WizardShell.slint`: header + step indicator + shared
  `WizardFooter` (Back/Cancel/Next); create/adopt/scaffold page
  stacks (create:41-152, adopt:36-138, scaffold:33-128) become page
  sets inside the shell.
- Struct-param dialogs for pure forms (record/schedule/transfer/
  confirms): struct in, single result callback out — delete the
  placeholder `view.cancelled = lambda: None` ×13.
- **Accept:** dialogs.py LOC down ≥ 40% from 1327; every dialog's
  cancel/confirm covered by tests; full dialog screenshot matrix
  (dark + light spot checks).

### RM-6 — Accessibility + keyboard (closes audit a11y item)

- `accessible-role` + `accessible-label` on every interactive element
  (buttons, list rows, pills, tabs, switches) — audit-gated by grep
  count (target: every `Button`/`TouchArea` hit has a role or an
  explicit exemption list).
- Keyboard: **Esc closes dialogs** (FocusScope on dialog root —
  probe-verified), arrow keys + Home/End + type-ahead in selection
  lists (selection_list.slint rows today are click-only TouchAreas),
  documented focus order, visible focus state token.
- App shortcuts via probe-verified mechanisms only: Ctrl+N (New),
  Ctrl+O (Adopt), F5/Ctrl+R (Refresh), Ctrl+F (focus filter) —
  probe `Shortcut`/`Key` handling first; anything unsupported drops
  to a documented limitation.
- **Accept:** keyboard-only walkthrough script executed and attached
  (navigate → open dialog → cancel with Esc → select row with
  arrows); a11y grep gate green; no mouse required for the 5 core
  flows.

### RM-7 — Responsive + light-theme QA (closes ML-2 residue, UXB-6, ML-3)

- Breakpoints 800/1100/1400 via width conditionals (probe-verified):
  sidebar density, app-bar wrapping, action-row reflow; Overview's
  7-button row wraps cleanly <800px (audit item).
- Dialogs: max-width + scroll past 600px; cards never clip actions.
- Overview dashboard-above-fold + danger-zone weight (ML-3 scope).
- Light-theme visual QA across all tabs against RM-1's roles: fix
  every contrast/role miss found (this is the promised QA pass, not
  a redesign).
- **Accept:** dark + light × 3 widths × 6 tabs screenshot matrix
  attached; audit findings closed or explicitly deferred with reasons.

## Closure (post-RM-7)

- Rewrite `design-system.md` + `patterns.md` in Slint/Material terms
  (kill Qt/GTK wording: `qt_style.qss`, `ui_qt`, `test_no_pyside_in_core`).
- Work-log entry for the remake; then the **owner's live pass of
  `slint-v2-checklist.md` (34 boxes) → tag `v3.0.0`** — unchanged gate,
  this plan does not replace it.
- File the upstream slint-python race issue (draft has sat too long)
  — carries regardless of this plan.

## Process

- Order: RM-0 → RM-1 → RM-2 → RM-3 → RM-4 → RM-5 → RM-6 → RM-7.
  RM-1/RM-2 are the visual-safety-critical ones (screenshots every
  step); RM-3 and RM-4 each land as sub-steps (4a-4e) with green
  suites between.
- Each sub-step: ruff + suite + relaunch + screenshots. Baseline from
  RM-0 is the comparison artifact.
- Rollback rule: a sub-step that changes screenshots beyond its
  declared allowance is reverted or fixed before the next starts.
- Non-goals: feature work, custom fonts, dynamic color, WebView,
  ripple, sidebar collapse, Dev Mode Watch, RadioGroup (broken), bulk
  start/stop (stays deferred), i18n.

## Status

- [x] RM-0 guardrails (enforcement tests + duplication counters +
      baseline screenshot; `tests/test_no_slint_styles.py` 4 tests,
      allowlist 104 entries / ceiling 104, counters 73/48/17;
      `tools/shots.py` + `make shot`, baseline
      `docs/shots/20261001-132033-rm0-baseline-default.png` — full
      3-width matrix stays the manual pass; suite 407 green)
- [x] RM-1 token truth (Theme.dark via `Palette.color-scheme` — probed
      live-updating + forceable; type ramp + subtitle/weight/mono
      tokens; spacing scale completed 4/8/12/16/24/32 (renumbered,
      all still unused so no look change); scheme-aware semantics —
      dark keeps legacy gray/red/orange/green, light tuned
      #616161/#b3261e/#b36b00/#146c2e, chips fixed; all 104+ sweep
      hits → tokens incl. ternaries; ⚠ prefixes removed ×4 (color
      carries danger); allowlist emptied, ceiling → 0; suite 407,
      ruff clean, light+dark shots verified)
- [ ] RM-2 component library (ActionButton, EmptyState, SectionHeader,
      StatusPill/ErrorBanner/Snackbar, LineList, FormField, DataTable;
      views + dialogs migrated)
- [ ] RM-3 shell remodel (store.slint global + structs, callbacks out
      of AppWindow, prop ceiling test, nesting/indent sweep)
- [ ] RM-4a fix `_own` cancel clobber (+ tests)
- [ ] RM-4b dispatch registries (drain + actions, equivalence)
- [ ] RM-4c dedup + dead-code removal
- [ ] RM-4d UI-thread I/O hygiene (shell write, tail read)
- [ ] RM-4e controller split behind UiPort
- [ ] RM-5 dialog + wizard framework (DialogDriver[T], registry,
      WizardShell, struct-param dialogs; dialogs.py −40%)
- [ ] RM-6 a11y + keyboard (roles, Esc, arrow nav, shortcuts)
- [ ] RM-7 responsive + light-theme QA (breakpoints, dialog scroll,
      overview fold, screenshot matrix)
- [ ] Closure: design-system/patterns de-Qt, work-log, owner v2
      checklist pass → v3.0.0 tag
