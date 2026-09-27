# Retro: what the GTK → Qt migration taught us about planning big work

Written at the close of PSQ-10. Intended audience: whoever plans the
next multi-sprint initiative. Every claim below happened at least once.

## 1. Foundation sprint first, no exceptions
PSQ-1 (shell + threading model + guardrails + headless testing) made
every later sprint boring in the good way: no sprint reinvented
threading, styling, or test setup. Cost: one sprint. Savings: every
sprint after it. `docs/qt-architecture.md` is the template — copy its
shape (layering rules, one threading discipline with a code example,
headless-testing standard), not just its content.

## 2. Shared components before features that need them
PSQ-2 built SelectionList/dialogs/toasts before any screen used them.
Payoff was direct and measured: Discover (PSQ-4), Module Install (PSQ-5),
and Addon Manager (PSQ-6) each became wiring work, and the one real
component bug (CheckState uncheck no-op) was fixed once, in one place,
then re-verified per screen. Contrast with the GTK history this
replaced: the same selection UI built fresh per screen, each with its
own slightly different bugs.

## 3. A human pass is a hard gate, not a nice-to-have
The white-on-white rendering bug (every dim label invisible) passed a
268-green suite without a ripple. Only a person looking at a real screen
caught it — same as the double-toolbar, shared-scrollbar, and clipped
checkbox bugs before it. Schedule the pass explicitly, screenshot
everything, and treat "looks wrong but I can't say why" as a finding.
Settle screenshots before grabbing (half-painted frames read as bugs).

## 4. Core vs frontend bugs are separate concerns, separate commits
The odoo_shell reader race lived in core/ but surfaced in Qt testing.
It was reported, approved, and fixed as its own commit — never folded
into a feature sprint. The one time a core-adjacent shortcut tempted
(parallel track workers vs read-modify-write), the fix stayed in the
frontend layer and core/ stayed untouched for the entire migration
(verified by guardrail on every run).

## 5. Guardrails compound; review discipline doesn't
Four static guardrails now run on every suite invocation (no-GUI-in-core
× 2, unbound-globals, dead-callbacks). Each was written the day its bug
class first bit, and each has caught a real regression since — including
one the same day it was written (missing QListWidgetItem import). Write
the guardrail in the sprint that discovers the bug class, not later.

## 6. Test teardown is product behavior in miniature
Three separate aborts traced to QThread lifetime at teardown (GC'd
workers, still-running threads at window close, no-drain script exits).
The production closeEvent drain exists because the test suite forced the
issue first. Rule of thumb established: if a test needs
wait_for_background to exit cleanly, the app needs the same path on
quit — implement it in the product, not just the test.

## 7. E2E scripts: restore-then-verify, and distrust your own greens
Two separate occasions where a passing check was vacuous (untrack
asserted against an already-clean registry; a "restored OK" that never
verified). Standard now: every E2E mutation ends with an independent
read-back against the source of truth, and residue from earlier runs is
cleaned before — not after — the run that needs a pristine state.

## 8. Report evidence, not conclusions
"Verified byte-identical", "exit 0 with screenshots", "fails pre-fix /
passes post-fix" — every fix in this migration shipped with the command
that would prove it wrong. Claims without that ("should cover", "works
on my reasoning") were sent back, correctly, every time.

## 9. Addendum: visual-design stretch (Part B + busy indicator)

- **QToolBar action-widgets lie about visibility.** A widget added via
  `toolbar.addWidget()` reports stale `QWidget::isVisible()` (True when
  hidden) — verified against a bare toolbar where the same calls behave.
  Consequence: never gate logic or assertions on a toolbar child's
  visibility; the busy indicator lives in the status bar for exactly
  this reason. If Qt ever needs toolbar-resident state again, re-probe
  first — do not assume.
- **Screenshots need settle time.** Grabbing immediately after a tab
  switch captures half-painted frames that read as missing widgets.
  Settle ~2.5s before grabbing; when a screenshot shows something odd,
  reproduce with settled captures before filing it as product.
- **Stale-frame rule, stated once:** a screenshot is evidence only if
  you viewed it yourself before writing about it. Twice now, reading
  the pixels caught what the test run missed (white-on-white text,
  shoved-off-screen button).
- **Design tokens need a consumer audit, not just a definition.**
  UX-1.1 defined the scale; Part B found screens never governed by it.
  A token system is done when every screen is checked against it, not
  when the doc is written.
- **Destructive flows deserve their own dialogs, not flag parameters.**
  The managed-remove dialog (typed name + per-DB drop checkboxes) was
  ported as a real dialog because the alternative — overloading the
  simple remove() path — would have hidden the dangerous options.
  Same principle as the wizard pattern: match the ceremony to the risk.
