# Odoo Vite — Sprint 9 Spec: Logs UI Fixes, Addon Manager Consolidation, Performance Pass

**Depends on:** Sprints 1–8 (Configuration/Modules pending your final human passes — this sprint fixes issues found during use, can proceed alongside those passes finishing up).

---

## Ticket 9.1 — Wire Log Search into the Logs tab UI
- `log_search.py`'s backend already exists (Sprint 8, B.2) but isn't reachable from the Logs tab shown in the screenshot — only Pause/Run Doctor/duration/Profile are visible.
- Add a visible search bar + filter controls (regex input, level dropdown, optional time range) to the Logs tab, wired to the existing backend. Results can jump-to-match in the tail view or show in a small results panel — whichever B.2 already implemented on the backend side, just make sure it's actually reachable, don't re-architect it.
- **Test:** search for a known string in a real log from the Logs tab UI itself (not just via a unit test against the backend function) and confirm results appear.

## Ticket 9.2 — Clarify/confirm "follow" behavior
- Confirm the existing Pause/auto-scroll behavior from Sprint 8 (B.1) already satisfies "follow the log always" — if the Pause button's un-paused state already means "always follow new lines as they arrive," this may just need a clearer label (e.g. "Follow" toggle instead of "Pause," so its *on* state reads as the feature name) rather than new logic. If there's a real gap (e.g. it doesn't resume following automatically after being paused, or loses position on tab-switch), fix that specifically. Report which it was — a labeling fix or a real behavior gap.

## Ticket 9.3 — Consolidate the Addon Path Manager (fix a UI regression)
- There are currently two different addon-path UIs: the original dialog (Sprint 7, has Enable/Disable + Remove + reorder) and a newer one reachable via "Manage..." on the Configuration tab (missing Enable/Disable, only has Remove + reorder). This is a regression — the feature request for disable-instead-of-remove already exists in the codebase, it just didn't make it into whichever dialog is now the one users actually reach.
- **Fix:** one single Addon Path Manager, reachable consistently (pick one entry point — likely the "Manage..." link makes sense as the only one, but confirm nothing else links to the old one and leaves it orphaned/inconsistent). It must have: Add (with folder browse), **Edit** (change the path string of an existing entry, not just remove-and-re-add — useful if a folder gets renamed/moved), Enable/Disable (per the original request: don't lose the reference, just exclude it from the derived `addons_path` string, this already exists in `addon_paths.py`'s data model per Sprint 7 — just make sure the UI actually exposes it), Remove (full deletion), Reorder (up/down, as already built).
- **Test:** disable a path, confirm it stays listed but is excluded from the written `addons_path`; re-enable it, confirm it's included again; edit a path's string, confirm the conf reflects the edit on next Apply.

## Ticket 9.4 — Performance pass
- No single reported bug here — this is a proactive profiling pass. Focus areas, in priority order:
  1. **`get_statuses()` polling loop** (every 2s across all instances, Sprint 3) — confirm it's not doing anything heavier than necessary per tick (e.g. avoid redundant `psutil.Process()` construction, cache what's safe to cache within a single tick, confirm CPU/memory overhead is genuinely negligible with several instances registered, not just one).
  2. **Log tail rendering** (Sprint 8, B.1) — the 50k-line test already proved the initial load is fast; confirm sustained tailing (lines arriving continuously over a long period, e.g. a chatty instance left running for an hour) doesn't accumulate memory or slow down over time, not just at initial load.
  3. **Discover Databases / List Modules** — both can return large lists (17+ databases, 634+ modules seen in your own testing); confirm the search/filter UI stays responsive as you type, not just that the initial load is fast.
- Report actual measurements (rough timings/memory, not just "it feels fine") for whichever areas you check, and only make changes where you find a real issue — don't speculatively rewrite something that's already fine.

---

## Out of scope for Sprint 9
- Developer Tools (next sprints)

## Report-back template
```
Ticket 9.1 (Log Search wired into UI): [done/blocked]
Ticket 9.2 (Follow behavior — label fix or real gap): [done/blocked] + which
Ticket 9.3 (Addon Manager consolidation, incl. Edit): [done/blocked]
Ticket 9.4 (Performance pass): [done/blocked] + measurements + what changed (if anything)
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
