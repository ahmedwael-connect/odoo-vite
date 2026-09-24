# Odoo Vite — UI/UX Enhancement Initiative: Master Plan

**Scope:** a dedicated multi-sprint effort to bring visual and interaction consistency across the whole app, following the same pattern as the original project charter — sequenced sprints, current one fully specified, later ones previewed and refined once we get there. **No new features in this initiative** — same rule as the refactor sprint. This is entirely about how the app looks, feels, and behaves to use, not what it does.

**Why this is its own initiative, not scattered fixes:** every UI bug we've caught so far (overflow, duplicate toolbars, invisible checkbox states, unclear loading feedback) was found and fixed in isolation, in whatever screen it happened to show up in. That's fine for bugs, but it means the app has never had one consistent design language applied *deliberately* — it has whatever each sprint's UI needs produced, sprint by sprint, for eleven sprints. This initiative is the deliberate pass.

---

## Sequence overview

| Sprint | Focus | Status |
|---|---|---|
| UX-1 | Design System Foundation + full visual audit | **Fully specified below — start here** |
| UX-2 | Shared Selection Widget (replaces every ad hoc list/checkbox/dropdown) | **Fully specified below** |
| UX-3 | Dialog & Form Consistency (confirmations, destructive actions, spacing) | Previewed, detailed later |
| UX-4 | Feedback & State Consistency (loading/empty/error states, toasts) | Previewed, detailed later |
| UX-5 | Navigation & Layout polish (tabs, sidebar, resizing, responsiveness) | Previewed, detailed later |
| UX-6 | Accessibility & keyboard navigation pass | Previewed, detailed later |

Each sprint after UX-2 will be specified once we see what UX-1's audit actually surfaces — I'd rather plan those with real findings in hand than guess at problems that might not be the real ones.

---

## Sprint UX-1 — Design System Foundation + Audit

### Why this comes first
Every later sprint in this initiative needs something to conform *to*. Right now, spacing, colors, and typography are whatever each screen's original ticket happened to produce — there's no single source of truth. This sprint creates that source of truth and catalogs every place the app currently deviates from it, but **does not fix most of those deviations yet** — cataloging first means later sprints fix things against a real, complete list instead of discovering problems piecemeal again.

### Ticket UX-1.1 — Design tokens
- Create a single shared stylesheet/resource (`ui/style.css` or equivalent, loaded once at app startup via a `Gtk.CssProvider`) defining: a spacing scale (e.g. 4/8/12/16/24/32px — pick a consistent scale, don't invent ad hoc values per screen), a small set of semantic colors that work with libadwaita's light/dark theming rather than fighting it (success/warning/error/info, reusing whatever accent colors libadwaita already exposes rather than hardcoding hex values that won't adapt to theme changes), and consistent corner-radius/border values.
- Audit current font usage — confirm headings/body/monospace (log views) are using a small, deliberate set of sizes, not whatever each screen happened to pick.
- **Deliverable:** the stylesheet itself + a short `docs/design-system.md` documenting the scale and when to use what, so this doesn't silently drift again in sprint 12.

### Ticket UX-1.2 — Full visual audit (catalog only)
- Go through every screen/dialog in the app (Overview, Databases, Modules, Configuration, Logs, Dev Tools tabs; every wizard; every dialog — Discover, Addon Manager, Remove confirmation, Restore, etc.) and catalog, against the new tokens from UX-1.1:
  - Spacing/padding inconsistencies
  - Color usage that doesn't match the semantic set (e.g. a warning shown in a color other than the established warning color)
  - Font size/weight inconsistencies
  - Any remaining instances of the long-string-overflow bug class (per `docs/patterns.md`'s rule from Sprint 8 — this audit is a good forcing function to confirm that rule actually got applied everywhere, not just where a user happened to report it)
  - Any widget that looks meaningfully different from a similar widget elsewhere (e.g. if there are three different visual styles of "confirm this destructive action" across the app, that's exactly the kind of drift this sprint should catch)
- **Deliverable:** a findings document (`docs/ux-audit-findings.md`), organized by screen, each finding tagged with severity (cosmetic / meaningfully confusing / actually broken). This becomes the input to UX-3 through UX-6 — don't fix findings in this ticket unless something is trivially a one-line fix you notice along the way; the point of this ticket is the catalog, not the fix.

### Ticket UX-1.3 — Icon audit
- Catalog every icon currently used (buttons, status indicators, sidebar) — confirm they're all coming from one consistent icon set (libadwaita/Adwaita's symbolic icon theme, ideally) rather than a mix of styles. Note any inconsistencies for later fixing.

---

## Sprint UX-2 — Shared Selection Widget

### Why this is its own sprint
Selection UI (checkbox lists, dropdowns, pickers) appears more than anywhere else in this app — Discover Databases, the Addon Path Manager's path list, Model Inspector's model picker, Record Browser's domain builder and results list, Module List's filter/multi-select for Install, the Database switcher dropdown, Cron Jobs list — and every single one of these was built independently, in its own sprint, with its own ad hoc widget. The specific complaint that triggered this whole initiative (Discover Databases' unclear checkbox states) is a symptom of that: **there is currently no one "this is how selection works in Odoo Vite" component, so every screen reinvented it slightly differently, and some reinventions were worse than others.**

### Ticket UX-2.1 — Design and build the shared component
- `ui/widgets/selection_list.py` (new): one reusable component covering the actual range of selection patterns this app needs:
  - **Single-select** (radio-style — e.g. picking a database to switch to)
  - **Multi-select with checkboxes** (e.g. Discover's track-selection, Module Install's multi-pick)
  - **Grouped** (sections with headers — Discover's Likely/Other/Uninitialized groups, potentially useful elsewhere too)
  - **Searchable/filterable** (a built-in filter entry, not bolted on per-screen each time)
  - **Scrollable with a sane height cap** (per the H-P1 fix, now built into the component itself rather than re-implemented per dialog)
- **Checked/unchecked visual state must be unambiguous without hovering** — this was the literal original complaint, and the H.M... style fix from that ticket should become this component's *default* behavior, not a patch applied to one dialog. Use a clearly filled/checked icon state + a distinct (not hover-only) row treatment for selected rows, consistent with whatever UX-1.1 establishes as the semantic "selected" treatment.
- Support an optional per-row secondary label/badge (needed for things like Discover's version-mismatch indicator, or Module List's state badges) without needing a special-cased fork of the component for each use.
- Support keyboard navigation (arrow keys to move, space to toggle a checkbox, type-to-search) — this is genuinely new capability most current implementations probably don't have; note if any do already and what pattern they use, reuse it if it's good.

### Ticket UX-2.2 — Migrate existing selection UIs onto the shared component
Go through every existing selection UI and replace it with the new component, **one at a time, testing after each**, same incremental discipline as the refactor sprint:
- Discover Databases dialog (the original complaint — do this one first, it's the most direct proof this sprint solved the actual problem)
- Addon Path Manager's path list
- Model Inspector's model picker
- Record Browser's results list + any filter/domain selection UI
- Module List's search/filter + Install's multi-select
- Database switcher dropdown (Databases tab)
- Cron Jobs list (if it has any selection interaction — check, may just be a read-only table, in which case skip)
- Any other selection UI found during UX-1.2's audit that I haven't listed here

### Ticket UX-2.3 — Regression pass
- Full test suite + a genuine continuous manual pass through every migrated screen (same "one continuous session, not stitched-together smokes" standard as the refactor sprint's R.9) — confirm every migrated selection UI still does what it did before, just with the new consistent look/behavior.

---

## Report-back template (for whichever sprint is currently active)
```
Ticket [X] : [done/blocked] notes...
Manual E2E (continuous, not per-ticket): [pass/fail] notes...
Deviations: ...
Open questions for PM: ...
```

---

## Note on sequencing with other work
This initiative should run **after** the refactor sprint's final verification closes (no point restyling code that's mid-reorganization) and can proceed independently of whatever comes after the v2.0.0 tag on the feature side — UX-1 and UX-2 don't touch `core/` at all, so there's no real conflict with parallel backend work if that becomes relevant later.
