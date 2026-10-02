# UX Overhaul Plan v2 — capability-grounded (UXB-0 → UXB-6)

This replaces the UXA sprints (same goal, rebuilt from what Slint
1.18b1 provably supports — every item below was verified with a compile
probe or the reference, never assumed from "next version" docs).

Style verdict stands: material pinned in `main.py`, env-overridable.

## Verified capability inventory (the design material)

| Capability | Proof | UX use |
|---|---|---|
| `Palette` theme brushes (background/foreground/alternate/control/accent/selection/border) | probe ok | surfaces that follow light/dark automatically |
| `Switch`, `StandardButton` (ok/cancel…), `ProgressIndicator` | probe ok | modern toggles, native dialog roles, determinate/indeterminate busy |
| `Tooltip` (takes `@markdown`) | probe ok | full paths, button hints, badge meanings on hover |
| SVG icons via `@image-url` + `Button.icon`/`icon-size` | probe ok | a real icon language (new/play/stop/refresh/…) |
| `StyledText` + `@markdown` | probe ok | rich readouts (bold key + plain value in one block) |
| `GridBox` + `Row` | probe ok | form grids (deletes the 110/140/150px label hacks) |
| `StyleMetrics.layout-padding/spacing` | probe ok | spacing tokens from the style itself |
| `TableColumn` min-width/stretch/sort-order + sort callbacks | reference | sortable DB table, columns that fit 800px |
| `Text` max-lines+elide, letter-spacing, alignment | reference | clamps, eyebrows, right-aligned numbers |
| `animate`/`states` | in use (pill fade) | keep minimal |
| `RadioGroup` | BROKEN in 1.18b1 (title-only shell) | ComboBox stays for mode pickers — recorded, not retried |
| Forced `color-scheme` | NOT exported in 1.18b1 | system-follow only (`material-dark` via env) |
| ListView programmatic scroll | absent | Follow toggle stays (D1) |

## What the code audit says (mapped to capabilities)

- ~35 hardcoded `gray`s + fixed red/orange/green/white: readable in
  both modes today, but surfaces (sidebar, cards, pills) ignore the
  theme — Palette adoption fixes dark mode structurally instead of
  by luck.
- Zero icons, zero tooltips, CheckBoxes doing toggle duty (Follow,
  Remember), verbs-only dialog buttons (StandardButton fits
  Ok/Cancel/Close spots), busy only at the bottom (ProgressIndicator
  belongs next to the acted button).
- Form label widths disagree per file; readouts are "Key: value"
  soup; DB columns have no widths and no sorting.
- Log tail is proportional (shell is mono); no level tinting.

## Sprints

### UXB-0 — Theme foundations (`theme.slint`)

- `theme.slint` globals importing `Palette` + `StyleMetrics`: spacing
  tokens, dim role (fixed gray kept deliberately — readable both modes,
  documented), semantic red/orange/green kept, surface roles mapped
  (sidebar → `alternate-background`, cards → default, selection →
  `selection-background`, borders → `border`, primary highlights →
  `accent-background`).
- Sidebar gets a real surface (Rectangle + alternate-background) —
  instant app structure, free dark mode.
- **Accept:** toggling OS/system dark (or `material-dark`) breaks
  nothing; no `gray` outside the dim role; screenshots both schemes.

### UXB-1 — Icon language + tooltips

- `ui_slint/icons/*.svg` set (new, adopt, play, stop, refresh, add,
  delete, search, folder, check, warn, download, upload, info, close):
  minimal geometric SVGs, `currentColor`-friendly; `Button.icon` +
  `icon-size` on action buttons app-wide.
- `Tooltip` with full text on every elided path, badge, table cell
  overflow, and icon-only button.
- **Accept:** no `…` without a hover path to the full text; every
  action button has icon + text; icon-only buttons have tooltips.

### UXB-2 — Control modernization

- Follow + Remember + plaintext-opt CheckBoxes → `Switch`; dialog
  Ok/Cancel/Close → `StandardButton` where verbs allow (Cancel/Close
  everywhere; verb buttons like "Drop permanently" stay custom);
  `ProgressIndicator` (indeterminate) next to the acted button row on
  search/doctor/profile/update-code/test-run while busy.
- Preferences mode stays ComboBox (RadioGroup broken — recorded).
- **Accept:** toggles read as toggles; dialogs get native keyboard
  roles; busy is visible at the point of action.

### UXB-3 — Grids, readouts, tables

- All forms onto `GridBox` (one label width everywhere — the hacks go);
  readouts become `StyledText` (`**Key** value`) inside aligned grids;
  DB table gets min-width/stretch columns + clickable sortable headers
  (bridge sorts the cache — ascending/descending round-trip).
- Log tail goes monospace; level tinting only if it fits the string
  models cleanly (stretch goal, droppable).
- **Accept:** forms align across dialogs; table sorts + fits 800px;
  tail readable.

### UXB-4 — Shell + per-tab assembly

- Shell rebuild on the tokens/icons/tooltips/grids (app bar already
  exists in plan UXA-1 — folded here to avoid two shell rewrites):
  in-window top bar (instance + status chip + New/Adopt/Refresh),
  sidebar Instances/System sections on the alternate surface, status
  strip with overlay snackbar.
- Then the six tabs, one by one, composed from the finished
  components — no new patterns invented mid-tab.
- **Accept per tab:** 3-second scan, 800px fit, screenshots.

### UXB-5 — Feedback, motion, keyboard

- Warning banners (toned) replace red inline server notes; empty/
  loading states unified; pill fade kept + row-press states if free;
  tab-order + Enter/Esc pass.
- **Accept:** every async op shows busy at action + completion;
  keyboard pass clean.

### UXB-6 — Responsive + final

- Breakpoints 800/1100/1400 (max content width decision made live);
  dialogs scroll past 600px; cards never clip actions; dark + light
  screenshot set per tab.
- **Accept:** v2-checklist UX items empty; screenshot set attached.

## Process

- Order: UXB-0 → UXB-1 → UXB-2 → UXB-3 → UXB-4 → UXB-5 → UXB-6. Each
  sprint: ruff + suite + relaunch + **screenshots at 3 widths, both
  schemes where easy**. No pixels, no pass.
- Probes before promises: anything not in the inventory table gets a
  `/tmp/probe` compile check before it enters a sprint.
- Non-goals: features, custom fonts, dynamic color, sidebar collapse,
  WebView, Dev Mode Watch, ripple/custom paint.

## Status

- [x] Style verdict: material (pinned, env-overridable)
- [x] Capability inventory (probed against 1.18.1b1)
- [x] UXB-0 theme foundations (theme.slint globals; sidebar on
      alternate-background surface)
- [x] UXB-1 icons + tooltips (18-SVG set, action buttons app-wide,
      danger-button hints, row + conf-line hover paths)
- [x] UXB-2 controls (Switch toggles; StandardButton cancel/close
      dialog roles; ProgressIndicator log-busy at action)
- [x] UXB-3 grids + tables (GridBox forms everywhere; StyledText
      readouts; sortable DB table with ★ pin + size bytes; mono tail)
- [x] UXB-4 shell + tabs (app bar with instance/status/global
      actions; sidebar Instances/System sections; reserved snackbar
      strip; page padding on all views)
- [x] UXB-5 feedback + keyboard (server-note bordered banner;
      Enter-to-search on log + record filters; empty/loading unified;
      press states via bold+pointer)
- [ ] UXB-6 responsive + final
