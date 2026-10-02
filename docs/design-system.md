# Odoo Vite Design System

Single source of truth for app styling — **web frontend edition**
(React + `src/style.css`; the GTK/Qt/Slint stacks this doc used to point
at were removed in the web cutover).

Enforced mechanically by `tests/test_style_guardrails.py`:
1. **hex colors only inside theme blocks** (`:root` / `[data-theme=...]`)
2. **font-size only via the type ramp** (`var(--fs-*)`)
3. **margin/padding/gap only from the spacing scale**

## Spacing scale (px)

`4 / 8 / 12 / 16 / 24 / 32` — nothing else (1px/2px allowed as hairlines
only, e.g. row separators and dense stream lines):

| Token | Use |
|---|---|
| `--space-1` (4) | tight: icon-text gaps, dense rows |
| `--space-2` (8) | compact: related controls in a row, button padding |
| `--space-3` (12) | default: dialog content, card padding |
| `--space-4` (16) | section: tab content margins, separated groups |
| `--space-5` (24) | loose: wizard pages, major sections, toast inset |
| `--space-6` (32) | page: top-level breathing room, rare |

## Themes

Dark is the default. `theme.ts` resolves the saved choice
(system/dark/light) onto `<html data-theme="dark|light">`; token overrides
live in the `[data-theme='light']` block of `style.css`. **All colors are
tokens** — component rules must never carry a hex literal (guardrail #1).

Persistence is two-layer, because browser storage is ephemeral under
WebKitGTK (no localStorage; sessionStorage/cookies die with the process):
a fast session chain (localStorage → sessionStorage → cookie) plus the
registry **settings table** (`app.theme()` / `app.save_theme()`), which
survives restarts and hydrates the session layer on boot.

Token groups:

| Group | Tokens |
|---|---|
| Surfaces | `--bg`, `--panel`, `--panel-2`, `--border` |
| Content | `--text`, `--muted` |
| Roles | `--accent` (+`--accent-hover`, `--on-accent`), `--ok`, `--bad`, `--warn` |
| Role tints | `--accent-bg`, `--ok-bg`, `--warn-bg`, `--bad-bg` (banners, focus halo, subtle fills) |
| Effects | `--overlay-bg`, `--shadow-1/2/3` |
| Motion | `--dur-fast` (100ms), `--dur` (160ms), `--ease` |

New color need? Map to a role, add it to **both** theme blocks. Never
invent a per-screen color.

## Type ramp

No literal font sizes outside the theme blocks (guardrail #2).

| Token | Size | Use |
|---|---|---|
| `--fs-display` | 20 | the one big number/title per screen (rare) |
| `--fs-title` | 16 | `.title`, `.sidebar-title`, `.app-title` |
| `--fs-heading` | 14 | `.heading` card titles |
| `--fs-body` | 13 | body, buttons, `.section-header` |
| `--fs-label` | 12 | `.field-label`, compact table cells |
| `--fs-caption` | 11 | `.pill`, `.chip`, `.sel-badge`, `th`, `.busy-dot` |
| `--fs-mono` | 12 | `.mono`, `.linelist-line`, streams |

Classes: `.title` / `.heading` / `.dim-label` (secondary) / `.mono`
(code/paths/streams). Long values elide — see `patterns.md`.

## Corners, elevation, form labels

- `--radius-card` 12px: `.ov-card`, `.modal`. `--radius-ctl` 6px: buttons,
  inputs, lists. `--radius-pill`: pills/chips. No per-screen radii.
- Elevation: `--shadow-1` cards, `--shadow-2` toasts, `--shadow-3` modals.
  Borders + shadow together — do not add ad-hoc shadows.
- `--form-label-w`: the one label-column width (`.kv`, `.gap-row`,
  addons rows). Do not hand-set 110px/140px columns.

## Motion

Enter animations only: modal `modal-in`, overlay `fade-in`, toast
`snack-in`; hovers/transitions use `--dur-fast`/`--dur` + `--ease`.
Everything is disabled under `prefers-reduced-motion` (global rule) —
never add an animation that bypasses it.

## Focus & states

- `:focus-visible` → accent outline ring (global). Inputs additionally
  get the `--accent-bg` halo on `:focus`.
- Disabled buttons: token `opacity: 0.55` (never lower — contrast floor).
- Selected rows: `.sel-row.selected` (bold + accent, always visible,
  never hover-only). Databases table reuses it — no second idiom.

## Semantic classes

| Class | Role |
|---|---|
| `.status-running` / `.status-stopped` / `.status-error` / `.status-draft` | status pill roles (`.status-stopped` neutral gray = deliberate) |
| `.error` | error text (red) |
| `.warn` / `.warning` | warning text — **not** for errors |
| `.dim-label` | secondary text |
| `.empty-state` | empty-slot copy only — not errors/loading/success |
| `.discover-picked` | legacy alias of the selected treatment (migrating to `.sel-row.selected`) |

## When to use what (quick rules)

1. New screen? Start from the tokens; don't invent values.
2. New color? Map to a semantic role in both themes.
3. New "selected" look? Reuse `.sel-row.selected`.
4. Tempted by a one-off style? It belongs in the kit (`components/ui.tsx`)
   or in this doc — not inline in a view.
