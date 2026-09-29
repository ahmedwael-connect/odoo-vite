# Slint UI/UX Improvement Plan (UXS-1 → UXS-4)

Live testing caught a whole bug class headless tests cannot see: they
invoke callbacks directly instead of simulating clicks, so **dead click
paths ship green**. This plan fixes that class plus the readability and
feedback gaps found auditing every `.slint` view, before PSS-5b resumes.

Ground rules (from `slint-full-plan.md` §2) still bind: no new product
features (polish only), main-thread Slint ownership, in-place model sync,
weak callbacks, owned dialogs, ruff + suite + live proof per sprint.

## Audit findings (what's wrong today)

1. **Schedules list is not selectable (functional bug).**
   `databases.slint` renders `ListView { for s in sched-rows: Text {...} }`
   with no click handling — `sched-row` never leaves -1, so Edit / Run /
   Toggle / Delete always toast "Pick a schedule first". Same bug class
   as the sidebar TouchArea (fixed). The DB table is fine
   (`StandardTableView` has native selection).
2. **Toast has no severity.** Inline bottom `Text`, info and errors look
   identical; the shared `ToastOverlay` component exists but the shell
   doesn't use it.
3. **No busy indication outside Modules.** DB ops (backup/restore/switch/
   validate/discover) leave buttons enabled mid-flight → double-run risk.
   Modules already does this right (`mod-busy` reactive disable).
4. **Progress log overflows.** `progress_view.slint` renders the whole log
   in a bare `Text` — long runs overflow the dialog, no scroll.
5. **Destructive actions look safe.** Remove / Drop / Uninstall / Restore /
   Delete buttons are visually identical to Refresh (Slint `Button` has no
   destructive role — needs a text/color convention).
6. **Button-row overflow.** 7 Databases buttons in one `HorizontalLayout`
   clip on narrow windows; no grouping by intent.
7. **Long paths force width.** Qt had the `elided_middle` rule; Slint views
   show full paths (`ov-path`, addon paths), widening tabs unnecessarily.
8. **Ad-hoc typography.** Heading sizes 16/18/20px mixed per view, no
   shared spacing/padding convention.
9. **Flat Overview.** Status is plain text; fields ungrouped; enterprise
   badge unstyled.
10. **Missing empty states.** Schedules and sidebar go blank instead of
    explaining themselves (Modules already does this right).
11. **No dark mode.** Slint supports it natively; currently day-only.

## UXS-1 — Dead interactions (P0, correctness)

- Clickable schedule rows: `TouchArea`-wrapped rows (same pattern as the
  `SelectionList` fix), new `sched-picked` callback → bridge sets
  `sched-row`, visible selected highlight, empty-state text
  ("No schedules yet — press Add").
- Audit pass: every `for`-rendered row in every view must have a working
  select path; `StandardTableView` current-row paths re-verified live.
- **Accept:** every Edit/Run/Toggle/Delete/Deps/Uninstall button reachable
  by mouse alone on a scratch instance; new live click-checklist started
  (see §Process).

## UXS-2 — Feedback & safety (P1)

- Toast severity: `toast-kind` (`info` / `error`) prop; errors render red
  with a `⚠` prefix, info stays neutral; route the shell through the
  shared overlay positioning; keep the 5s auto-hide.
- `db-busy` prop mirroring `mod-busy`: set on progress-op start, cleared
  on `progress-done`; all DB action buttons bind `enabled` to it.
- Progress dialog: log inside a `ScrollView` pinned to the bottom on new
  lines; keep the 500-line cap.
- Destructive convention: confirm labels carry the verb + `color: red` on
  the action button for Remove / Drop / Uninstall / Restore / Delete
  (documented in the convention note, applied everywhere at once).
- **Accept:** error toasts visibly distinct; no button double-fires during
  a 10s+ op; 500-line log stays inside the dialog; every destructive
  button styled.

## UXS-3 — Readability (P1)

- Shared convention note (5 lines: heading scale, section-label style,
  spacing/padding, then applied mechanically to all views).
- Databases buttons regrouped in two labeled rows (Inspect: Discover /
  Refresh / Validate · Danger: Init / Drop · Transfer: Backup / Restore /
  Files); schedules get their labeled section already present.
- `elide_middle` Python helper in `ui_slint` (Qt rule ported) applied to
  display-only paths (`ov-path`, addon rows, file paths); full text kept
  in bridge state for future use.
- Overview hierarchy: colored status pill (green Running / gray Stopped),
  grouped Server (version/port/path) vs Database (primary/user) lines,
  styled enterprise badge (keep texts).
- Schedules rows go two-line: `cron · state` / `dbs · last run` (dim).
- **Accept:** no tab forces horizontal growth from a path; headings and
  spacing uniform across the three tabs; overview scannable in 3 seconds.

## UXS-4 — Polish (P2)

- Dark mode enablement: turn on Slint `dark-mode` support, check every
  view + dialog for hardcoded colors that break (keep the full PSS-9
  theme sweep where it is).
- Sidebar: enable the built-in filter (`show-filter`) + wire it for large
  registries; verify selected-row highlight is visible.
- Window: `min-width`/`min-height`, title binds the selected instance
  (`"Odoo Vite — <name>"`, falls back to plain title).
- Sidebar empty state ("No instances yet").
- **Accept:** app usable dark and light; 10-instance registry navigable;
  window survives resize to minimum.

## Process

- Order: UXS-1 → UXS-2 → UXS-3 → UXS-4 (correctness before polish).
- Each sprint ends: ruff clean, full suite green, live X11 proof **plus
  the click-checklist** (every button/row on every tab clicked by a human
  or a driver-level picked/toggled invocation — the gap that let two dead
  click paths ship).
- Headless tests where logic is involved (severity mapping, `elide_middle`,
  schedule pick/paint); pure-visual changes verified live only.
- Non-goals: icons, keyboard shortcuts, new features, dialog Enter-to-
  confirm (beta focus risk — revisit post-cutover), the PSS-9 theme sweep.

## Status

- [x] UXS-1 dead interactions (sched rows clickable + picked highlight +
      empty state; for-loop audit: SelectionList + schedules only)
- [x] UXS-2 feedback & safety (toast info/error kinds incl. kind-aware op
      sinks; db-busy gating; progress ScrollView pinned bottom;
      destructive ⚠ red headings on confirms, typed tier always)
- [x] UXS-3 readability (convention note in app.slint; Databases
      regrouped Inspect/Danger-zone/Transfer + sched buttons 3+3;
      elide_middle ported, ov-path elided with accessible full text;
      Overview status pill + Server/Database groups; two-line sched rows)
- [x] UXS-4 polish (dark mode: system-follow verified by audit, no
      hardcoded backgrounds — nothing to enable; sidebar filter wired;
      window min sizes; title follows selection; sidebar empty state)
- Then: PSS-5b Configuration (per `slint-full-plan.md` §4).
