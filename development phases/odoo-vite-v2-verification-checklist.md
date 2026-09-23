# Odoo Vite — v2.0.0 Verification Checklist (Sprints 5–11)

**Purpose:** one structured pass covering everything built since the last tag (v1.0.1). Work through it in order — later sections assume earlier ones are in a known-good state. Doesn't need to be exhaustive on every sub-detail; the goal is confidence, not perfection. Note anything that feels off even if you can't articulate exactly why — "this felt slow" or "this wasn't what I expected" is useful signal on its own.

For each section: ✅ if it worked as expected, ⚠️ with a note if something was off but not blocking, ❌ with a note if something's actually broken.

---

## 1. Database Operations (Sprint 5)
- [ ] List Databases tab shows size/owner/initialized/version for tracked databases
- [ ] Discover Databases: grouping (Likely/Other/Uninitialized) is correct, only "Likely" pre-checked, checkboxes clearly show selected state
- [ ] Initialize a not-yet-initialized database standalone (not via first-Start)
- [ ] Drop a non-primary database — type-to-confirm required, primary database has no Drop option available at all
- [ ] Backup a database, confirm the dump file + sidecar JSON are created
- [ ] Restore that backup, confirm data comes back correctly (spot-check a record)
- [ ] Validate DB Config on a working instance — report should come back clean

## 2. Module Management (Sprint 6)
- [ ] List Modules — search and state filter (Installed/Upgradeable/Installable/All) work
- [ ] Install a module, confirm it actually appears usable in Odoo afterward
- [ ] Update Modules — watch all three stages (git/pip/-u) show distinct progress, confirm backup-before-update checkbox behaves as expected (default-on, optional)
- [ ] Uninstall a module, confirm it's actually gone
- [ ] Module Diff — spot check a couple of entries against what you know to be true
- [ ] Dependency Graph renders (d3/WebKit view) — if it doesn't, confirm the native list fallback shows instead
- [ ] Scaffold a module, install it, confirm Odoo accepts it without complaint

## 3. Configuration Management (Sprint 7 + Sprint 9 fixes)
- [ ] Configuration tab: no more overflow/off-screen text (Sprint 9 fix) — check at a realistic/smaller window size, not maximized on a huge monitor
- [ ] Edit a common conf key, save, confirm "next restart" notice appears correctly when the instance is running
- [ ] Restore last backup after a conf edit, confirm it reverts correctly
- [ ] Regenerate from registry — confirm the destructive warning appears and manual edits are correctly lost/replaced
- [ ] Addon Path Manager (single consolidated dialog, Sprint 9): Add, **Edit** (rename/move a path), Enable/Disable (path stays listed, excluded from conf when disabled), Remove, Reorder — all work correctly, round-trip verified in the actual conf file
- [ ] Instance metadata: description, workers (try `workers > 0`, confirm the port pre-check behaves), log level, custom python interpreter

## 4. Logging & Monitoring (Sprint 8 + Sprint 9 fixes)
- [ ] Logs tab: Follow toggle (renamed from Pause) — confirm it actually follows new lines, and pauses cleanly when you scroll up
- [ ] Log Search is visible and reachable directly from the Logs tab (Sprint 9 fix) — search for something, confirm results
- [ ] Run Doctor against a real or deliberately-broken instance, confirm findings are useful/accurate
- [ ] Slow Query Analysis — confirm the detect-and-instruct message is clear if `pg_stat_statements` isn't enabled
- [ ] Flame Graph — run one against a running instance, confirm the SVG opens/displays
- [ ] Event Log Panel — open it, trigger a few actions elsewhere in the app, confirm events show up live without freezing the UI

## 5. Developer Tools — Part 1 (Sprint 10)
- [ ] Model Inspector — pick a model you know well (e.g. `res.partner`), confirm fields/constraints/access look right
- [ ] Record Browser — search/filter, then **carefully** test create/update/delete on a throwaway record only; confirm delete requires typing the display name, confirm update shows a before/after diff before committing; confirm `ir.*` models cannot be deleted through this tool at all
- [ ] Cron Jobs list shows real jobs with sane next-run times
- [ ] Generate launch.json — open the generated file, confirm the paths look correct for your setup
- [ ] Open in VS Code / Cursor — confirm whichever you have installed opens the right folder

## 6. Developer Tools — Part 2 (Sprint 11)
- [ ] Odoo Shell — open it, run a simple read-only command (e.g. list some users), confirm real output comes back
- [ ] Dev Mode Watch — turn it on for a test instance, edit a file in its custom_addons, confirm **exactly one** automatic restart happens (not zero, not several)
- [ ] Run Tests — run a module's tests, confirm it refuses/warns appropriately if you try to point it at the primary database, confirm the disposable `<primary>_test` naming makes sense to you as a default

## 7. General/cross-cutting
- [ ] App version shows correctly in the header (top-left area)
- [ ] Nothing anywhere feels meaningfully slower than before, especially with several instances registered
- [ ] Any screen you spend real time on — does anything about the layout, spacing, or wording feel unpolished, even if technically correct? Note it even if minor.

---

## When you're done
Send back this checklist with your ✅/⚠️/❌ marks and notes. If it's clean (or only minor ⚠️ notes), I'll sign off on tagging **v2.0.0**. If there are real ❌ items, we'll scope one consolidated fix sprint for those specifically rather than reopening each original sprint separately.
