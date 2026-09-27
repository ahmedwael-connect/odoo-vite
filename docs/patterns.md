# Odoo Vite — Engineering Patterns (living checklist)

Read this before adding a screen or a core module. Most of these exist
because we got burned once — the note says where.

## UI text containers (H-M1, Sprint 8 A.1 — twice-bitten rule)

**Any new text container showing a value that could be long — paths, conf
values, instance/database names — must use max-width + elide.**
Check this before shipping any new screen. Concretely, on every `QLabel`
bound to user data or a path:

```python
label.setWordWrap(False)
label.setTextInteractionFlags(Qt.TextSelectableByMouse)
label.setMinimumWidth(0)
label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
# + stylesheet max-width / Qt.ElideMiddle for paths, ElideRight for names
# (see ui_qt/views/configuration.py refresh_conf() for the pattern)
```

`wrap=True` alone does NOT save you: comma-joined paths and URLs have no
break opportunities and still blow out the container. (History: GTK used
`Pango.EllipsizeMode`; same rule, Qt mechanism.) Wizards got this in
H-M1; the Configuration tab and sidebar got it in Sprint 8 A.1.

## Layering (Phase 1 §1.1, enforced by test)

- `core/` is pure Python: zero GUI imports (`tests/test_no_gtk_in_core.py`
  and `tests/test_no_pyside_in_core.py` fail the suite otherwise). GUI
  crossing happens in `ui_qt/` via queued signal/slot delivery, never by
  importing UI modules from core. (Pre-cutover history: GTK `ui/` is deleted;
  same rule, Qt mechanism.)
- Long operations run on `QThread` workers; the GUI thread is never blocked.

## Errors (Phase 1 §1.5)

- Core functions return `Result(ok, message, data)`, never raise into UI.
- Toasts for resolved/transient events; persistent label + status pill for
  ongoing bad states (PM decision, Sprint 4).

## Ground truth over flags (RC BUG-3, Hotfix H-B3, Sprint 5 Part A)

- Never trust a cached flag (`db_created`, "drop needed") when Postgres can
  answer directly. `core/db_state.py::get_db_state()` is the single owner
  of exists/initialized/version/size/owner — no per-caller re-derivation.
- Destructive paths verify-then-act; unreachable server aborts loudly
  rather than assuming "missing".

## Conf writes (Sprint 7)

- Validate before writing; refuse with specifics; never leave half-written
  files. One rolling `odoo.conf.bak` before every overwrite + one-click
  restore. Unknown ini sections are preserved, never dropped.
- Registry is the source of truth for structured data (addon paths list);
  the conf string is derived from it, not vice versa.

## Privilege (Sprints 1–3, Phase 1.5)

- `pkexec` for system-user escalation (never raw sudo from the GUI).
- Least-privilege roles by default in managed mode; DB create/drop are
  explicit, separately-privileged, user-confirmed operations.
- Secrets live in the OS keyring; no silent plaintext fallback (explicit
  opt-out only, audited).

## Confirmation tiers
- Type-to-confirm: managed remove, standalone DB drop, restore-into-existing.
- Light confirm: adopted unregister, switch-to-new-DB (via Start flow).
- Always show the exact command/database when creation or deletion is on
  the table — never a generic "are you sure".

## Test layout at scale (REG.3, same class as Sprint 11 devwatch dotfiles)

- Any layout/rendering change must be exercised against a realistically
  large dataset for at least one tab/list (the odoo_19_adopted instance's
  1500-module list is the standing fixture) — sizing/scroll bugs
  involving shared containers only manifest at scale, never on small test
  instances.
- Each tab owns its scroll container (`_scroll_wrap` per tab); never one
  shared scroll area around a multi-tab widget — the tallest tab drives
  the viewport for all the others.
