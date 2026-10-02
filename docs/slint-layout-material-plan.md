# Layout Rescue + Material System Plan (ML-0 → ML-3)

Trigger: live screenshots show the frontend is broken, not just unpretty —
Overview actions unreachable below the fold, Databases controls stretched
to giant sizes, no scroll anywhere to recover. Two defects combine:

1. **Stretch-everything layouts.** Not one `.slint` file sets `alignment`
   on any layout. Slint box layouts stretch children to fill surplus
   space, so every VerticalLayout distributes empty window area across
   its children (huge gaps, huge ComboBox/LineEdit/Buttons), and long
   tabs push trailing controls (Start/Stop, Save, Close) off-screen.
2. **No scroll roots.** Tab content taller than the window is simply
   unreachable — buttons exist but cannot be seen or clicked. Every
   previous "live proof" ran maximized, which hid both defects.

This plan rebuilds layout foundations first, then the Material system on
top. It supersedes the cosmetic parts of `slint-material-redesign-plan.md`
(MX-2 card rollout caused the worst of it — cards + stretch compose
terribly); MX-0/MX-1/MX-3 items already shipped stay shipped.

## ML-0 — Layout rescue (P0, correctness, first, ships alone)

Rule for every view, no exceptions: **root column gets
`alignment: start`** (pack to top at preferred sizes, never distribute);
exactly one stretch element per tab owns surplus space (the main list:
tail, module list, shell output via `vertical-stretch: 1`); button rows
and inputs keep preferred heights everywhere.

- Overview: pill + Server/Database cards + action row pack to top; no
  gaps; actions always visible without scrolling at 640px+.
- Databases: switch card, table (bounded height, virtualized — never a
  stretch victim), action rows at preferred height, schedules list owns
  surplus.
- Every other tab: same treatment — audit each HorizontalLayout row for
  vertically-stretched inputs (ComboBox/LineEdit/SpinBox get no stretch).
- Dialogs: same rule (create/adopt/scaffold/record/addons содержаt tall
  rows by the same bug class — check each).
- **Accept:** at 800×500 minimum AND maximized: no giant controls, no
  mystery gaps, every button from the screenshots reachable by mouse;
  screenshots at both sizes attached to the sprint.

## ML-1 — Scroll architecture (P0, with ML-0)

No content may be unreachable, ever. Per-tab scroll contract:

- Tabs with one virtualized list as body (Modules, Logs tail, Databases
  table area): NO outer scroll — the list owns surplus via stretch and
  scrolls internally. Everything else packs above/below at preferred
  size so the list always has room.
- Tabs with many sections and no dominant list (Configuration,
  DevTools): whole tab goes in a `ScrollView`; inner `ListView`s that
  would grow unbounded get fixed `max-height` caps (search/doctor/cron/
  shell already have them; models/records SelectionLists get caps —
  e.g. 220px — so virtualization survives inside the scroll root).
- Dialogs taller than 600px get the same treatment (scaffold/record).
- **Accept:** with 10 instances, 20 schedules, 50 records on screen, at
  800×500: every control reachable by scroll or click; no clipped
  buttons; inner lists still virtualize (spot-check with 1500 modules).

## ML-2 — Material system (P1, Google-style apps)

Slint ships a `material` widget style (boots clean — verified). ML-2
starts with a live A/B: current native vs `SLINT_STYLE=material`, judged
from screenshots, then ONE of:

- (a) Adopt material style app-wide (launcher + docs pin it), or
- (b) Keep native + hand-rolled M3 kit in a shared `theme.slint`:
  `M3Button` (filled/tonal/outlined/text variants as Rectangle +
  TouchArea + press-state), `M3Card` (toned container + title slot),
  M3 color roles as global properties (primary, on-primary,
  error-container, warning, dim), M3 type ramp.

Either way, applied per tab after: consistent button hierarchy (one
filled primary per action row), cards restyled to the winning system,
status pill + badges + toasts/snackbar re-skinned once, empty states and
section headers unified. No custom paint beyond rectangles — ripple and
elevation shadows are out (Slint limits, not fought).

- **Accept:** side-by-side screenshots per tab before/after; one style
  system, zero ad-hoc colors outside the role list; dark + light pass.

## ML-3 — UX flow pass (P2, with real eyes on the new foundation)

- Overview becomes a dashboard: status + primary actions ABOVE the fold
  always (Start/Stop/Remove/Clone/Export pinned in a top action bar,
  details in cards below) — the screenshot proves details-above-actions
  buries the point of the tab.
- Databases: switch controls compact single row; danger actions get
  persistent visual weight (not just confirm-time red).
- Every destructive flow re-checked for reachability + clarity.
- Keyboard: Tab-order sanity, Enter-to-confirm where safe.
- **Accept:** the v2 checklist's "unpolished?" item comes back empty;
  timed task pass (select → start → backup → find a module) without
  hunting.

## Process (learned the hard way)

- Every UI sprint ends with screenshots at 800×500 AND maximized,
  attached by the tester — "live proof" without pixels hid this whole
  defect class for nine sprints.
- Headless tests still cover logic/dispatch/paint only; a new
  `docs/screenshots/` folder (git-ignored) holds the acceptance shots.
- Order: ML-0 → ML-1 → ML-2 → ML-3. ML-0 ships alone (bug fix).
- Non-goals: new features, icons, WebView graph, Dev Mode Watch,
  dynamic color, custom fonts.

## Status

- [x] ML-0 layout rescue (alignment:start on all roots + columns;
      one stretch owner per tab/list; inputs at preferred heights)
- [x] ML-1 scroll architecture (Databases/Configuration/DevTools in
      ScrollView; models/records pickers capped at 220px; virtualized
      stretch lists left unscrolled)
- [ ] ML-2 Material system
- [ ] ML-3 UX flow pass
