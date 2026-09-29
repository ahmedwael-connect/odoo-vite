# Odoo Vite v3.0.0 — Slint Verification Checklist (PSS-9)

Re-run of the v2 checklist against the Slint frontend on real Ubuntu +
Postgres. Same marking: ✅ expected, ⚠️ off-but-not-blocking with a
note, ❌ broken with a note. Tag `v3.0.0` on ⚠️-only; any ❌ gets one
consolidated fix sprint.

Known Slint divergences (accepted during migration, not re-litigated
here — confirm each behaves as documented, not that it matches Qt):

- D1. Follow is an explicit toggle (ListView exposes no scroll
  position); polls continue while paused, appends stop.
- D2. Pre-update backup is manual (Databases tab) — no auto-backup
  offer, no checkbox.
- D3. Dependency graph is the native two-pane viewer (no WebView).
- D4. Dev Mode Watch is a placeholder (needs a file-watch design).
- D5. Model metadata is a one-line summary, not the full
  fields/constraints/access view.
- D6. No event-log dock (audit log remains on disk).
- D7. Cancel leaves Create drafts in place (removable from sidebar).
- D8. Tail cap 2000 rows (display-only).

## 1. Database Operations
- [ ] Databases tab shows size/version/status per tracked DB, ★ primary
- [ ] Discover grouping (Likely/Other/Uninitialized), only Likely checked
- [ ] Initialize a non-initialized DB standalone
- [ ] Drop a non-primary DB (typed confirm); primary refuses with guidance
- [ ] Backup → dump + sidecar on disk; restore → data back (spot-check)
- [ ] Validate renders clean on a working instance
- [ ] Schedules: Add/Edit/Run/Toggle/Delete/Files all operate on the
  clicked row; empty list explains itself

## 2. Modules
- [ ] List/search/state-filter; Install Checked works end-to-end
- [ ] Update Checked; Update Code shows all three stages in progress
- [ ] Uninstall (typed); Deps viewer shows both panes
- [ ] Scaffold via Modules → New Module…; installs cleanly (D8: scratch DB)
- [ ] Conf saves carry the "(takes effect on next restart)" note

## 3. Configuration
- [ ] Tab fits 800px, no overflow (MX-0); common-key edit → save → restore reverts
- [ ] Regenerate shows the destructive confirm and replaces manual edits
- [ ] Addon manager: Add/Edit/Enable/Remove/Reorder round-trip into conf
- [ ] Metadata: description, workers>0 port pre-check, log level, custom interpreter Browse + executable validation

## 4. Logs
- [ ] Tail follows with Follow on (D1); Clear empties the view only;
  rotation/missing file noted, not crashed
- [ ] Search with level filter renders; Doctor findings useful
- [ ] Slow queries: clear guidance without pg_stat_statements
- [ ] Profile a running instance → toast + SVG auto-opens

## 5. DevTools
- [ ] Connect (wrong password red; Remember persists); model pick →
  metadata line + first page; domain search + paging (D5 noted)
- [ ] Record New/Edit (diff preview)/Delete (typed, ir.* refused) on
  throwaway data only
- [ ] Cron list real; launch.json paths sane; VS Code/Cursor opens folder
- [ ] Shell: Start → read-only command echoes; Stop flips status
- [ ] Tests: refuses primary, `<primary>_test` default sensible; needs a
  real run on a scratch DB

## 6. Wizards
- [ ] Create: branch → syscheck → details validation (each gate blocks
  with its error) → provision streams; Cancel/Retry/Discard behave;
  full create on a scratch instance
- [ ] Adopt: live reparse line; gaps editable; adopt lands on the instance
- [ ] Scaffold covered in §2

## 7. App-level
- [ ] Sidebar New…/Adopt…; Preferences flip persists; unchanged closes silently
- [ ] Export → Import round-trip on scratch data; bundle is 600 perms
- [ ] About Slint shows app version + disclosure widget (license term)
- [ ] Title follows selection; sidebar filter; error toasts red; busy
  gates hold during long ops; 800px and 1400px both clean
- [ ] `python3 main.py`, `python3 -m odoo_vite.main`, desktop launcher
  all boot the Slint app with an empty log
- [ ] Nothing feels slower; note any unpolished screen even if minor
