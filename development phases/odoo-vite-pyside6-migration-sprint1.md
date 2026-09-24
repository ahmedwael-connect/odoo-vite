# Odoo Vite — Frontend Migration: GTK4 → PySide6 (Qt6)

## Master sequencing

| Sprint | Focus |
|---|---|
| PSQ-1 | Foundation: app shell, threading model, design tokens, coexistence strategy, headless testing — **fully specified below, start here** |
| PSQ-2 | Shared component library — selection widget built right *from day one*, dialog/wizard patterns, toast/notification system |
| PSQ-3 | Instance sidebar + Overview tab + lifecycle flows (start/stop/restart/remove/switch/track/discover) |
| PSQ-4 | Database Operations tab |
| PSQ-5 | Module Management tab |
| PSQ-6 | Configuration tab (conf editor, Addon Path Manager — built correctly the first time using PSQ-2's shared selection component) |
| PSQ-7 | Logs & Monitoring tab |
| PSQ-8 | Dev Tools tab |
| PSQ-9 | Wizards (Create Instance, Adopt Instance, Scaffold Module) |
| PSQ-10 | Cutover: remove GTK `ui/`, finalize packaging, full verification pass |

Later sprints specified once we get there, same pattern as every prior multi-sprint initiative in this project — plan the next one with real findings in hand, not guesses made now.

## Strategic decisions (apply to every sprint in this migration)

**1. This is a coexistence migration, not a big-bang rewrite.** The GTK `ui/` stays fully intact and functional throughout — nothing gets deleted until PSQ-10, after the Qt version has full feature parity and has been verified. This is the same incremental, reversible discipline that worked well for the internal refactor sprint (separate commits, test after each move, nothing large and unreviewable). A frontend rewrite is a much bigger and riskier change than that refactor was; the same discipline matters more here, not less.

**2. `core/` does not change.** This is the entire payoff of Sprint 1's original architecture mandate — every database/module/config/process/backup function is already framework-agnostic. This migration only touches `ui/`. If any ticket in this migration finds itself needing to modify `core/` for a reason beyond "the Qt layer needs a slightly different data shape than the GTK layer expected" (which should be handled in a thin adapter, not by changing `core/` itself), stop and flag it — that's a sign something's being done wrong.

**3. New UI code lives in a parallel package, e.g. `odoo_vite/ui_qt/`**, not intermixed with `odoo_vite/ui/`. Keeps the two versions cleanly separable and makes PSQ-10's eventual deletion of the GTK version a clean, low-risk operation.

**4. Establish the same "GTK-free core" guardrail's Qt equivalent immediately**, per Ticket PSQ-1.3 below — this project has repeatedly benefited from automated architectural guardrails (`test_no_gtk_in_core.py`) rather than relying on review discipline alone; do the same thing for the new layer from the start rather than retrofitting it after drift happens.

---

## Sprint PSQ-1 — Foundation

**Goal:** a Qt application that launches, shows a minimal shell (empty sidebar, empty content area, header), reads real data from the existing registry (proving `core/` really is reusable as-is), follows a defined threading discipline, and can be tested headlessly — with nothing yet resembling a finished feature. Same shape as the very first GTK sprint in this project: foundation first, features after.

### Ticket PSQ-1.1 — Dependencies and project setup
- Add `PySide6` to the project's dependencies. **Do not remove `PyGObject`/`gi` yet** — the GTK build stays fully functional per the coexistence strategy.
- Confirm the target PySide6 version and note it explicitly (pin it, same as any other dependency) — Qt has meaningful behavior differences across major/minor versions, don't leave this implicit.
- Verify PySide6 actually runs correctly on the target Ubuntu environment before writing any real code — check for any missing system packages it needs (similar in spirit to `system_check.py`'s existing prerequisite-detection pattern; note if PySide6 needs anything comparable and whether it's worth detecting/installing the same way).

### Ticket PSQ-1.2 — Minimal app shell
- `odoo_vite/ui_qt/main_qt.py` (new entry point — see PSQ-1.5 for how this coexists with the existing `main.py`): `QApplication` + a `QMainWindow` subclass.
- Layout skeleton matching the existing app's actual shape (don't redesign the information architecture in this sprint, just reproduce the existing structure in Qt): a sidebar area (will hold the instance list, empty/placeholder for now) and a central content area (`QStackedWidget`, will hold Overview/Databases/Modules/etc. tabs later, empty/placeholder for now).
- **Prove `core/` reuse works**: on startup, call `registry.list_instances()` (the real, existing function, unmodified) and render the results as plain text/rows in the sidebar placeholder — no styling, no interactivity yet, just proof that the exact same backend code powering the GTK app also powers this one without any changes.

### Ticket PSQ-1.3 — Threading model + architectural guardrail
- Define and document the Qt equivalent of the GTK app's `GLib.idle_add` discipline (never touch UI from a background thread; marshal results back to the main thread). The natural Qt pattern: a `QObject`-based worker moved to a `QThread`, communicating results back via **queued signal/slot connections** (Qt automatically marshals a signal emitted from a worker thread to a slot running on the main thread, when connected across threads — this is the direct equivalent of `idle_add`, use it the same way).
- Write this up in `docs/design-system.md` or a new `docs/qt-architecture.md` — be explicit and concrete (a code example of "the right way to run a background `core/` call and get its `Result` back to the UI safely") so every subsequent sprint in this migration follows one consistent pattern rather than each sprint inventing its own threading approach.
- **Add the automated guardrail**, mirroring `test_no_gtk_in_core.py`: a `tests/test_no_pyside_in_core.py` confirming `core/` never imports `PySide6`/`shiboken6`, same as the existing GTK guardrail. Cheap insurance against the exact kind of drift Sprint 1 already prevented once.

### Ticket PSQ-1.4 — Design tokens: port to QSS
- Reuse `docs/design-system.md`'s existing spacing scale and semantic color roles (from UX-1.1) — don't invent a new design language, translate the existing one into Qt's styling system (QSS, Qt's CSS-like stylesheet language — different syntax from GTK CSS, same underlying design decisions).
- **Resolve the light/dark theme question explicitly, don't default silently:** libadwaita automatically followed the GNOME system theme; Qt does not do this automatically out of the box. Decide and document: does the Qt version detect and follow the Ubuntu/GNOME system theme (possible via `QPalette` + reading the desktop's dark-mode setting, e.g. via the XDG desktop portal or `gsettings`), or does it ship its own light/dark toggle independent of the system? I'd lean toward following the system theme, consistent with how the GTK version behaved and with general Linux desktop app conventions — but this is worth a deliberate decision, flag your recommendation and reasoning back to me before locking it in.
- Deliverable: a base `qt_style.qss` + confirmation the main window renders using it (still just the empty shell from PSQ-1.2, now styled).

### Ticket PSQ-1.5 — Coexistence entry point
- The existing `python main.py` (or whatever the current launch command is) must keep launching the GTK version, unchanged, for the duration of this migration.
- Add a distinct way to launch the Qt version during development (e.g. `python -m odoo_vite.ui_qt.main_qt`), documented clearly (a note in the project's README or a `docs/` file) so nobody launches the wrong one by accident, and so you have an easy way to compare both versions side by side as the migration progresses.

### Ticket PSQ-1.6 — Headless testing
- This project has relied heavily on Xvfb-based headless GTK testing throughout. Qt has an equivalent: the `offscreen` platform plugin (`QT_QPA_PLATFORM=offscreen`). Get a basic smoke test running that boots the Qt shell headlessly and confirms it doesn't crash (mirroring the exact discipline already established for the GTK side).
- If `pytest-qt` is a good fit for this project's testing style (it generally is, for Qt), consider adopting it now, at the start of the migration, rather than retrofitting test infrastructure later once more UI code exists — flag your recommendation.

---

## Testing expectations
- `pytest` for the new architectural guardrail (PSQ-1.3).
- Manual/smoke: launch the Qt shell (both normally and headlessly via `offscreen`), confirm it shows real instance data pulled from the actual registry, confirm the GTK version still launches and works completely unaffected by any of this.

## Out of scope for PSQ-1
- Any real feature UI (that starts at PSQ-3, after PSQ-2 builds the shared components those features will be built from)
- Removing or modifying anything in the existing GTK `ui/` package

## Report-back template
```
Ticket PSQ-1.1 (dependencies + environment check): [done/blocked]
Ticket PSQ-1.2 (minimal app shell + core reuse proof): [done/blocked]
Ticket PSQ-1.3 (threading model + guardrail test): [done/blocked]
Ticket PSQ-1.4 (design tokens → QSS, theme decision): [done/blocked] — recommendation on system-theme-following
Ticket PSQ-1.5 (coexistence entry point): [done/blocked]
Ticket PSQ-1.6 (headless testing): [done/blocked] — pytest-qt adoption recommendation
Manual check: GTK version still fully functional, unaffected: [confirmed/issue found]
Deviations: ...
Open questions for PM: ...
```
