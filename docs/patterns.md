# Odoo Vite — Engineering Patterns (living checklist)

Read this before adding a screen or a core module. Most of these exist
because we got burned once — the note says where.

## UI text containers (H-M1, Sprint 8 A.1 — twice-bitten rule)

**Any new text container showing a value that could be long — paths, conf
values, instance/database names — must elide or wrap deliberately.**

Rule of three: **prose wraps, identifiers elide, streams clip.**

- Prose (sentences, help text): wrap normally.
- Identifiers (paths, URLs, names): `.elide` / `<ElidePath>` — and any
  elided row that can truncate needs a `title` with the full text
  ("no `…` without a hover path").
- Streams (logs, shell, progress): clip at a line cap (`LINE_CAP`),
  own scroll container.

`flex` containers must have `min-width: 0` (or `.elide`) on children or
the ellipsis never engages — comma-joined paths have no break
opportunities and blow out the container even with wrapping.
(History: same bug shipped twice — GTK `Pango.Ellipsize`, Qt `ElideMiddle`.)

## Poll-driven UI: diff-before-repaint

**Poll ticks and refresh callbacks must never re-render identical data
into a visual churn.** The 2s status poll, 1s log tail and 500ms shell
poll run forever; every unconditional state rebuild is per-tick flicker
and steals in-flight clicks.

- Fingerprint list inputs and skip `setState` when deep-equal.
- Partial poll dicts must NEVER blank rows they don't carry — absent key
  = keep current value.
- Filesystem/subprocess probes get caches at the poll site; core
  verify-then-act paths always probe live.
- With keep-mounted tabs (views stay in the DOM, hidden), background
  polls keep running by design — a returning tab shows live state, not a
  reset. Gate any *expensive* work on visibility if it ever hurts.

## Layering (enforced by test)

- `core/` is pure Python: zero GUI imports
  (`tests/test_no_gtk_in_core.py`, `tests/test_no_webview_in_core.py`
  fail the suite otherwise). UI crossing happens through `ops/` +
  `ui_web/api.py` (js_api domains), never by importing UI modules from
  core.
- Long operations never block the UI: Python side uses
  `asyncio.to_thread` / workers under `ops/*`, streamed via progress
  events; every pywebview `js_api` call runs on its own thread.

## Errors & feedback (one model)

- Core functions return `Result(ok, message, data)`, never raise into UI.
- **Transient** result → toast (`route({kind:'message'})`), level
  matches severity — errors always `level:'error'`.
- **Persistent state** in a view → `Banner`/`StatusPill` in place (never a
  toast on a 5s timer); notices with severity use `Banner kind=`.
- **Empty slot** → `EmptyState`. Never overload it with error/loading/
  success copy — errors are `.error`/error-toast, loading is
  `Spinner`/`Skeleton`, persistent notices are `Banner`.
- Warnings are `Banner kind="warn"`, not errors. A failed operation is
  red (`kind="error"`, `role="alert"`).
- Guard failures (wrong state, refused op) → error toast, not silence.

## Action placement (Quiet-Modern IA)

- One **primary** action per card, in the card header (right side).
- Secondary/danger actions with low frequency → overflow menu (kebab).
- **Implementation**: `Card` takes `actions` (header buttons, primary
  first) and `menu` (kebab `MenuItem[]`, `danger`/`disabled` per item);
  the header renders them via `.card-head`/`.card-actions`. View-level
  actions with no card (instance Remove/Clone/Export) live in the
  view-title-row kebab next to the `StatusPill`.
- Filter/toggle rows that are not actions (Logs toolbar, search rows)
  stay as body `.btn-row`s — a toolbar is not an action surface.
- Buttons carry `Icon` glyphs, never text glyphs (▲▼×◀▶) — except
  semantic data markers in cells/options (★ primary, ✓ on/off).
- Verb + `danger` styling + typed confirm for destructive ops; always
  show the exact command/database — never a generic "are you sure".
- Busy state belongs **at the acted button** (`loading` prop), with the
  global footer spinner only for whole-app operations.
- E2E note: kebab items render only while the menu is open; harnesses
  that click a label must find it as a visible `.view button` (or open
  the kebab first). Modal label lookups prefer `.modal button` first.

## Confirmation tiers

- Type-to-confirm: managed remove, standalone DB drop, restore-into-existing.
- Light confirm: adopted unregister, switch-to-new-DB (via Start flow).
- Always show the exact command/database when creation or deletion is on
  the table.

## Ground truth over flags (RC BUG-3, Hotfix H-B3)

- Never trust a cached flag when Postgres can answer directly.
  `core/db_state.py::get_db_state()` owns exists/initialized/size —
  no per-caller re-derivation.
- Destructive paths verify-then-act; unreachable server aborts loudly
  rather than assuming "missing".

## Conf writes (Sprint 7)

- Validate before writing; refuse with specifics; never leave half-written
  files. One rolling `odoo.conf.bak` before every overwrite + one-click
  restore. Unknown ini sections preserved, never dropped.
- Registry is the source of truth for structured data (addon paths list);
  the conf string is derived from it, not vice versa.

## Privilege (Sprints 1–3, Phase 1.5)

- `pkexec` for system-user escalation (never raw sudo from the GUI).
- Least-privilege roles by default in managed mode; DB create/drop are
  explicit, separately-privileged, user-confirmed operations.
- Secrets live in the OS keyring; no silent plaintext fallback (explicit
  opt-out only, audited).

## Test layout at scale (REG.3)

- Any layout/rendering change must be exercised against a realistically
  large dataset for at least one tab/list (odoo_19_adopted's ~1500-module
  list is the standing fixture) — sizing/scroll bugs involving shared
  containers only manifest at scale.
- Each scroll region owns its container (`.content`, `.sel-list`,
  `.linelist`, `.tail-lines`…) — never one shared scroll area around
  multi-section content; the tallest section drives the viewport for
  every other one.
