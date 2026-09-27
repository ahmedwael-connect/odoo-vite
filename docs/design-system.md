# Odoo Vite Design System (UX-1.1)

Single source of truth for app styling. The GTK build's tokens lived in
`odoo_vite/ui/style.css` (removed at PSQ-10 cutover); the Qt build carries
the same scale/roles in `odoo_vite/ui_qt/qt_style.qss` (+ dark variant),
loaded once at startup via `odoo_vite.ui_qt.theme.apply_theme()`.

## Spacing scale (px)

`4 / 8 / 12 / 16 / 24 / 32` — nothing else. When setting `spacing=` or
`set_margin_*` in code, pick the nearest scale value:

| Value | Use |
|---|---|
| 4 | tight: icon-text gaps, dense rows (`.ov-tight`) |
| 8 | compact: related controls in a row (`.ov-compact`) |
| 12 | default: dialog content, card padding (`.ov-dialog`, `.ov-card`) |
| 16 | section: tab content margins, separated groups (`.ov-section`) |
| 24 | loose: wizard pages, major sections |
| 32 | page: top-level breathing room, rare |

Pre-refactor code uses ad hoc values (2, 6, 10 seen in the wild) — do not
add new ones; migrate old ones when touching a screen (cataloged in
`docs/ux-audit-findings.md`).

## Semantic colors

Roles, never hex, for success / warning / error / info / selected. The Qt
build maps them to QSS in `qt_style.qss` / `qt_style_dark.qss` so they adapt
to light/dark theme changes. (History: the GTK build used libadwaita's
public named colors.) CSS classes:

| Class | Role |
|---|---|
| `.status-running` | success + bold |
| `.status-error`, `.error` | error (+ bold for status) |
| `.status-draft`, `.warning` | warning |
| `.discover-picked` | selected-row treatment: bold + accent, always visible, never hover-only |
| `.status-stopped` | neutral gray — deliberate exception; verified on both themes |

## Corners

`.ov-card` = 12px radius for grouped content. Dialogs/buttons keep the
toolkit default — don't set custom radii per screen.

## Typography

No explicit font sizes anywhere in app code — use the type classes
(`.title` / `.heading` / `.caption.dim-label` / `.monospace`, see
`qt_style*.qss`). (History: the GTK build used libadwaita type classes.)

Long strings in labels: elide (`Qt.ElideMiddle` for paths,
`Qt.ElideRight` for names) — see `docs/patterns.md`.

| Role | Class |
|---|---|
| Window/dialog titles | `.title` |
| Section titles | `.heading` |
| Body | default widget font |
| Secondary text | `.caption.dim-label` |
| Logs / code | `.monospace` (monospace QFont / QTextEdit) |

## When to use what (quick rules)

1. New screen? Start from the scale above; don't invent values.
2. New color? You don't need one — map to a semantic role.
3. New "selected" look? Reuse `.discover-picked` semantics (UX-2 component).
4. Tempted by a one-off style? Add it to the audit findings instead.
