# Changelog

All notable changes to Odoo Vite. Versions track the `update/X.Y.Z`
release branches; git tags use a `v` prefix. The latest published
release notes also live on the GitHub Releases page.

## 3.3.0 — 2026-10-04

### Added

- **Registry hygiene (Preferences → Maintenance)**: list and purge
  registry rows whose folders no longer exist. Running rows and real
  files are never touched, keyring passwords are cleared per row, and
  every purge is written to the audit log (#13b).
- **Pull Odoo Updates**: one dialog pulls every unique community /
  enterprise checkout — dirty-worktree and non-git prechecks,
  `fetch --prune`, fast-forward-only merge, per-checkout report with
  ahead/behind counts, cancel support; one failing checkout never stops
  the rest (Modules toolbar → "Pull Odoo Updates…").
- **GitHub integration**: keyring-backed token (validate / clear in
  Preferences → GitHub), branch listing (cached `ls-remote`, serves
  stale on network errors), install a repository straight into an
  instance's addons with optional `odoo-bin -i`, sync a module from its
  git root, and publish addons to GitHub (create-repository option,
  refuses secret-looking files, unborn-branch aware). Entry points:
  Marketplace → "From GitHub…", Modules → "Sync from GitHub…" /
  "Publish to GitHub…". The token is sent only to `api.github.com` (#E1).

### Fixed

- Marketplace `owner/repo` path traversal — `../..` escaped the cache
  directory before `rmtree`; branch names are validated too (#1)
- Browser dev-mock Proxy fallbacks: any missing mock method resolves to
  a shape-safe answer instead of white-screening the shell (#2)
- Database dumps stage to `.part` and are atomically renamed (#3)
- Backup pruning only counts successful runs; partial runs report
  FAILED instead of being treated as good (#4)
- `discard_draft` guards: cancelled wizards leave no half-created rows (#5)
- Identifier and path-segment validation for instance/schedule names (#6)
- psql password passed on stdin, never on argv (#7)
- RPC passwords redacted from error and report payloads (#8)
- `run_streaming` reader-thread deadline: no zombie child processes on
  hung output (#9)
- Async load races in Configuration (`loadSeq`) and Modules
  (`seqRef`) — stale responses can no longer clobber fresh state (#10, #11)
- Uninstall validates module names — no shell or JSON injection via
  module ids (#12)

### Changed

- Version bump 3.2.0 → 3.3.0 (`version.py`, `pyproject.toml`, README).

Verification: 469 pytest green, ruff clean, `tsc --noEmit` + vite build
clean, live-window e2e 25/25 on Ubuntu 24.04. The test suite is fully
hermetic: per-test `ODOO_VITE_*` seams (#13a) plus CI fallbacks
(in-memory keyring, default git identity, Postgres/py-spy skips) so the
GitHub Actions runner runs it green headless.

## 3.2.0 — 2026-10-04

### Added

- **Streamed, cancelable operations**: DB init / backup / restore and
  bundle export / import run through the progress dialog with a working
  cancel — minutes-long operations no longer freeze the UI.
- **Pending module updates**: "update on next start" chips for
  scheduled module updates (Overview + Configuration).
- **Addons-path auto-typing**: classify an instance's addons paths as
  community / enterprise / custom in one pass.
- **Save-dialog overwrite guard**: the UI asks before clobbering an
  existing file (`path_exists`), since pywebview/GTK never prompts.

## 3.1.0 — 2026-10-03

### Added

- **N2 quality-of-life**: filters for event log, configuration,
  schedules and crons; copy buttons (tail, conf, shell, toasts, event
  log) plus JSON/CSV export; untrack menu; py-spy auto-install when
  profiling; `detect_editors` PATH gating.
- **C1 live operations feed**: long-running operations stream into the
  statusline dialog with a palette command.
- **C2 health monitor**: `core/health.py` probes exposed through
  `api.app.health` with an Overview health strip.

### Fixed

- **B1–B16 bundle**: db_password never serialized to JS, file-dialog
  filters, backup delete confinement, schedule retention prefill,
  module preview flags, settings-migration race, backup-timer
  auto-repair, FilesDialog guard, config dirty-refresh guard,
  dialog-aware shortcuts, palette Escape/cap handling, Overview effect
  deps, StrictMode-safe confirm settle, offline-PG test coverage,
  unhandledrejection → error toast.

## 3.0.0 — 2026-10-03

Web cutover release: pywebview + React desktop UI (Qt → Slint → web
migration complete) — Marketplace with apps.odoo.com mirror, reviews and
zip install; Quiet-Modern visual overhaul; log search/filters;
addons-path manager. Full notes on the GitHub release page.
