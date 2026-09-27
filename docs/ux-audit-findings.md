# Odoo Vite UX Audit Findings (UX-1.2)

First consumer audit of the design system (`docs/design-system.md`) against
every screen, plus the backlog UX-2..6 left open by the migration. Severity:
**broken** (blocks a task), **confusing** (works but misleads), **cosmetic**
(polish). Fixed items stay listed with their ticket for history.

Audit method: code inspection of `odoo_vite/ui_qt/` at laptop width
(1000×660 default) + offscreen widget probes. A human screenshot pass is
still required before any release tag (migration-retro §3: greens don't
catch white-on-white) — see U3 in the update plan.

## 1. Token sweep (spacing scale 4/8/12/16/24/32)

`rg "setSpacing|setContentsMargins" odoo_vite/ui_qt/`:

| Value | Uses | Verdict |
|---|---|---|
| spacing 8 | 50 | ✓ compact (related controls) |
| spacing 4 / 12 | 3+2 | ✓ tight / dialog content |
| margins 12,12,12,12 | 8 | ✓ dialog content |
| margins 16,12,16,16 | 7 | ✓ tab content |
| margins 8,4,8,8 | 1 (events dock) | ✓ dense log strip, acceptable |

No off-scale 2/6/10 values remain in `ui_qt/` (they survive only in
`docs/` history notes). Dialog minimum widths (420–640) form a sane
ladder, not tokens — no change. **Scale: CLEAN.**

## 2. Findings

### Fixed this batch
- [x] **broken — Preferences did not exist.** README told users to "Open
  **Preferences**" but no dialog/menu existed (U2.1: `widgets/preferences.py`
  + toolbar button + File menu + 4 tests).
- [x] **confusing — Postgres outages surfaced only per-action.** Every DB op
  failed separately with no global signal (U5.4: persistent banner on the
  Databases tab, poll-driven, auto-clears on recovery).
- [x] **confusing — no way to duplicate an instance.** Only create-from-scratch
  or adopt (U5.1: Clone files+settings, honest no-DB-copy scope, running
  guard, keyring carried over).
- [x] **confusing — invisible disk cost.** GB-scale clones/venvs with no
  in-app measure (U5.3: `core/disk_usage.py` + Overview "Measure disk").
- [x] **cosmetic — backup dialog hid destructive retention.** Pruning deletes
  files but the hint didn't say so (U4: hint now states `-Fc` + prune rule).
- [x] **cosmetic — stale toolkit references.** GTK/Gio/GLib/`load_app_css`
  mentions in `core/` docstrings, `docs/`, `pyproject` (U1: reworded; guardrail
  test pins `events.py`).

### Open — cosmetic
- [ ] Overview button row is now 7 wide (Start/Stop/Restart/Remove/Clone +
  Open in browser + Measure disk on a second row). At 1000px it fits; below
  ~800px it wraps. Acceptable, revisit if a 7th action ever joins the row.
- [ ] Toolbar has 4 text buttons (New/Adopt/Events/Preferences) and no icons
  on some. `style_button` covers start/stop/restart/remove only. Optional:
  extend `ICONS` to toolbar buttons.
- [ ] `QToolBar` children still lie about `isVisible()` (retro §9) — state
  stays in the status bar by rule. No action; do not re-derive.

### Fixed this batch (update-2)
- [x] **confusing — clone left a dead venv with no path forward.** The clone
  note said "rebuild the venv" but no such action existed (U5.2: Overview
  warns when `venv/bin/python` is missing + "Rebuild venv now" streams
  `rebuild_venv` — fresh venv + requirements + pkg_resources verify — into
  a ProgressDialog behind an exact-command confirm; also fixed the
  busy-gating stuck-off bug on the security/venv buttons).
- [ ] No bulk start/stop. **Deferred deliberately:** sidebar is single-select
  by design (shared `SelectionList` component), and parallel lifecycle
  workers lose registry updates (qt-architecture §5: batch RMW in ONE
  worker). Bulk needs multi-select sidebar + a batched flow — a full ticket,
  not a drive-by.

### Open — a11y / keyboard (UX-6, untouched)
- [ ] No focus-order audit, no mnemonics, no Esc-to-close sweep on dialogs.
- [ ] Elided paths carry tooltips in conf table + SelectionList; verify the
  new Preferences/Clone dialogs match (spot-checked: yes for registry path,
  no for port spin — trivial).
- [ ] No i18n (all strings inline). Out of scope until a second locale is
  requested.

## 3. UX-2 selection migration check

Every picker renders through `SelectionList` (Discover, Addon manager,
Model picker, Records, Modules, DB switcher combo, Cron): **MIGRATED.**
No per-screen list widgets with bespoke check handling remain except
`QTreeWidget` (tracked DBs — a columned table, correctly not a list) and
the backup file browser (`QListWidget`, delete-gated — acceptable).

## 4. Screens checked (all pass at 1000×660 unless noted)

Sidebar / Overview (+Clone/Disk/Preferences wiring) / Databases (+banner) /
Modules / Configuration / Logs / Dev Tools / Create-Adopt-Scaffold wizards /
schedules dialog / remove dialog / clone dialog / preferences dialog.
