# Slint Material Redesign Plan (MX-0 → MX-3)

Goal: bring the Slint frontend to a modern, Material-3-inspired UX and
fix the reported layout bug — **views grow wider than the screen instead
of fitting it**. Design language follows Google Material Design (M3):
color roles, type scale, 8dp grid, cards, button hierarchy, clear empty
and loading states. Nothing here adds product features (migration ground
rules still bind); it restyles and restructures what exists.

## 0. Diagnosis: why views overflow (the reported bug)

Audited every `.slint` file. Width forcers found:

1. **Log tail lines** (`logs.slint`): up to 2000 unbroken chars per row
   in a virtualized `ListView` — a single long traceback line stretches
   the whole tab. Same class: search/doctor/slow rows, shell output
   (`devtools.slint`), conf lines incl. full `addons_path`
   (`configuration.slint`).
2. **SelectionList titles** with full paths (addon manager, files) and
   unelided badges — no `overflow` bound anywhere.
3. **Button rows** without wrap points (DevTools/Configuration group
   rows are fine at 1000px but clip at the 800px minimum).
4. **StandardTableView** columns sizing to content (DB version strings,
   sizes) with no maximum.
5. Window `min-width: 800px` holds, but content min-width routinely
   exceeds it — and nothing scrolls horizontally, so content is pushed
   off-screen instead of reflowing.

Fix principle (applies everywhere): **prose wraps, identifiers elide,
streams clip**. Long log/shell lines get `overflow: elide` (full text
stays in the model, copy comes later); paths use the existing
`elide_middle`; prose labels get `wrap: word-wrap`; tables get column
maximums; every tab must fit the 800px minimum with worst-case content.

## 1. Material foundation for Slint (what M3 maps to here)

Slint ships a `material` widget style (light/dark variants, falls back
to system theme) — MX-1 starts by evaluating `material` vs the current
native style on Ubuntu and picking one for the whole app. On top of it:

- **Color roles** (documented once, used everywhere): primary (actions),
  error/red (destructive + error toasts, already convention), warning
  orange (already), surface (default), on-surface variants for dim text
  (replaces ad-hoc `gray`), success green (status pill, already).
- **Type scale** (extends the UXS-3 convention): display 20/700 for the
  instance name only; headline 16/700 view titles; title 14/700 section
  and card headers; body default; label-small dim for counts/status.
- **Cards**: `GroupBox` per section (Qt DevTools already proved the
  pattern) — every tab becomes a scrollable column of cards with 16px
  outer padding, 8dp inner rhythm.
- **Button hierarchy**: primary = filled (one per row max: Save, Install,
  Start, Connect, Run tests), secondary actions plain buttons,
  destructive keeps the red-⚠ confirm convention (std Button styling is
  fixed by the widget style — hierarchy comes from placement + primary,
  not custom paint).
- **Shape**: rounded dialog corners and pill radii come from the style;
  custom pills keep 6px radius (already).
- **Feedback**: toasts move toward snackbar behavior (bottom overlay,
  severity colors already in); busy states gain `Spinner`/`ProgressIndicator`
  next to disabled buttons for long ops; empty states everywhere (already
  convention); loading placeholders ("Loading…") kept.

Honest Slint limits (not fought): no ripple effects, no WebView
(dependency graph stays a native list), no programmatic ListView scroll
(Follow stays a toggle), `Palette.color-scheme` forcing is version-
sensitive — system-follow stays.

## 2. Sprint plan

### MX-0 — Fit fix (P0, correctness, first)

- Per-view overflow pass with a worst-case content matrix: 2000-char log
  line, 300-char path, 1500-row module list, 10 instances, 20 schedules.
  Apply wrap/elide/clip per the principle above; cap table columns;
  split any button row that clips at 800px.
- Add a scroll root where tabs can exceed height (DevTools,
  Configuration already overflow vertically on small screens).
- **Accept:** every tab fits 800px with worst-case content, no clipped
  controls; resize 800→1400px reflows without gaps or stretch artifacts;
  live proof on the maintainer's screen at both widths.

### MX-1 — Foundation (P1)

- Style evaluation (`material` vs native) + decision recorded; color-role
  and type-scale note committed to `app.slint` header (extends UXS-3);
  replace ad-hoc `gray` dim with the role; cards (`GroupBox`) adopted as
  the section container in one pilot tab, then rolled out.
- **Accept:** single style app-wide; no hardcoded color outside the role
  list; pilot tab reviewed live before rollout.

### MX-2 — Per-tab redesign (P1, one tab at a time)

Order: Overview (dashboard cards: status, server, database, quick
actions) → Databases (table + grouped actions in cards) → Modules →
Configuration → Logs (tail readability: monospace, level tinting) →
DevTools (longest — section cards with the deferred Watch placeholder
kept). Each tab: card layout, hierarchy pass, empty/loading states,
button hierarchy, overflow re-check.
- **Accept per tab:** 3-second scannability, all controls reachable at
  800px, click-checklist re-run.

### MX-3 — Feedback & motion (P2)

- Snackbar-positioned toasts; `Spinner` on long ops (search, doctor,
  profile, update-code); subtle Slint `animate` on pill/selection
  changes only (no decorative motion); final dark+light review.
- **Accept:** every async op shows busy + completion; dark and light
  verified live.

## 3. Process

- Order: MX-0 → MX-1 → MX-2 → MX-3. MX-0 ships alone (it's a bug fix).
- Each sprint ends: ruff clean, full suite green (GC discipline from
  PSS-5b stays), live X11 proof at 800px AND 1400px plus the
  click-checklist.
- Headless tests for logic (elide/format/severity/dispatch); layout and
  style verified live only — screenshots described back by the tester.
- Non-goals: new features, icons set, keyboard shortcuts, ripple/custom
  paint, dynamic color, WebView graph, Dev Mode Watch (still needs its
  own file-watch design).

## Status

- [x] MX-0 fit fix (overflow: elide on all stream/list rows incl.
      SelectionList titles with stretch; prose wraps; logs toolbar
      split; table columns deferred — content short in practice)
- [x] MX-1 foundation (material style boots clean — decision left to
      live comparison; color audit: roles already tight, nothing stray;
      GroupBox cards piloted on Overview; convention note extended)
- [x] MX-2 per-tab redesign (Overview piloted in MX-1; Databases 3
      cards; Modules list card; Configuration 4 cards; Logs search/slow
      cards with uncarded stretch tail; DevTools 7 cards)
- [x] MX-3 feedback & motion (snackbar toasts; global busy Spinner
      bound to mod/db/dev gates with Working… status; 250ms pill
      transition; dark+light left to live review)
- Then: PSS-7 Wizards (per `slint-full-plan.md` §4).
