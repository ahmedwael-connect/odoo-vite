# Odoo Vite — Regression Investigation Sprint

**Status:** UI/UX initiative is **paused** at UX-1.1. Do not start UX-1.2 until everything in this sprint is resolved and re-verified. These bugs surfaced after the refactor sprint + UX-1.1, both of which were supposed to be behavior-neutral — that claim needs to actually hold before we build anything else on top.

**Why this is bisectable, not a mystery:** the refactor sprint landed as separate commits per ticket (0af875c, 94a37c9, 0a021c0, 9517b10, aafb55d, 5ec85a9, ed0c6db), followed by UX-1.1 (e3bd0ad). Use `git bisect` (or just manually check out each commit and re-test) against the specific repro steps below rather than guessing at root cause from reading code — we have the exact tool for this situation, use it.

---

## Ticket REG.1 — Addon Path Manager button does nothing
- **Repro:** open an instance's Configuration tab, click "Manage…" next to `addons_path`. Expected: the Addon Path Manager dialog opens. Reported: nothing happens.
- **Investigate:** this dialog's flow (`_addons_manage_dialog`) was moved in Ticket R.5 (commit 9517b10). Check first whether the button's signal is still connected to the right callback after the mixin/flow-class split — a common failure mode after this kind of refactor is a signal connected to a method reference that's now stale (e.g. connected to `self._addons_manage_dialog` on the page object when the method now lives on a `ConfigurationFlows` instance instead). Check the browser console / app's stderr for a swallowed exception — a callback throwing and failing silently (no visible error, just "nothing happens") is exactly what a broken reference after a refactor looks like.
- **Fix + regression test:** once found, add a test that would have caught this — likely a simple "button click actually invokes the flow method" wiring test, not just "the flow method works when called directly" (which is probably already tested and passing, hence this slipping through).

## Ticket REG.2 — Performance regression ("too laggy")
- **Investigate methodically, don't guess:** bisect across the refactor + UX-1.1 commits specifically checking for a perceptible slowdown at each point. Candidates to check first, in rough likelihood order:
  1. **CSS provider loaded incorrectly** — confirm `load_app_css()` is called exactly once at startup (per the report, it should be), not accidentally re-invoked on every window redraw or tab switch. A `Gtk.CssProvider` being reloaded/reparsed repeatedly is a classic cause of exactly this symptom.
  2. **The flow-class split itself** — confirm `XxxFlows(self)` instances aren't being **re-constructed** on every action instead of built once and reused (e.g. if a property recreates `DatabaseOpsFlows(self)` fresh on every access rather than storing it once in `__init__`).
  3. **Something interacting with Ticket REG.3's scrollbar bug** — if a tab's content is genuinely oversized/miscalculated, GTK doing constant relayout/size renegotiation against a broken natural-size calculation could itself cause real, measurable lag, not just a visual glitch. These two bugs may share one root cause — check REG.3 first, it might explain this one for free.
- **Deliverable:** actual before/after measurements (same style as Sprint 9's performance pass — real numbers, not "it feels better now"), at whichever commit turns out to be the actual regression point, plus after the fix.

## Ticket REG.3 — Shared/blank scrollbar leaking across tabs
- **Repro:** open an instance with a large Modules list (634 modules, per earlier testing), view the Modules tab, then switch to Overview (or another lighter tab) — the scrollbar/scroll area is reportedly shared/oversized across tabs, showing a long blank scroll on tabs with little content.
- **Root cause hypothesis to check first:** this is almost certainly from Ticket R.8 (commit ed0c6db, the `page_instance_detail.py` mixin-per-tab split). Check whether each tab's mixin has its **own** `Gtk.ScrolledWindow`/scroll container, properly parented and sized independently, versus all tabs sharing one scroll container at the page level whose natural size gets driven by whichever tab has the most content (this would exactly produce "Overview shows a huge blank scroll" if Overview is a small amount of content sitting inside a scroll area sized for Modules' 634 rows).
- **This is the same bug class as Sprint 11's Dev Mode Watch failure** — a refactor/feature that was verified against small/synthetic data and never exercised against realistic real-world data volume. Add the explicit lesson to `docs/patterns.md` alongside the existing dotfile-filtering entry: **"any layout/rendering change must be tested against a realistically large dataset for at least one tab/list in the app, not just a small test instance — GTK layout bugs involving shared sizing/scrolling often only manifest at scale."**
- **Fix + regression test:** ensure per-tab scroll isolation; add a test/manual-check step specifically using the largest real dataset available (the 634-module instance) as part of standard regression checking going forward, not just for this one fix.

## Ticket REG.4 — "Follow logs" not working correctly
- **Repro needed from PM/user before deep investigation** — the report doesn't specify exactly what's wrong (does it not follow at all? does it stop following after some time? does the toggle not respond?). **Before doing a deep bisect on this one, do a quick manual check yourself first and describe the actual failure mode precisely** — if it's easy to reproduce and obviously connected to the same refactor/CSS changes as the other tickets, fold it into the same bisection pass. If it's a distinct, unrelated bug, report it separately with a precise repro so this doesn't turn into guessing.

## Ticket REG.5 — Add a "Clear" action to the Logs tab
- This isn't a regression, it's a missing control that should have been part of the original Logs tab (Sprint 8) — add a "Clear" button that clears the **displayed** view (resets the tail view's rendered rows), not the actual log file on disk (never truncate/delete the real `logfile` — that's Odoo's own data, not something a "clear" button should destroy). Make this distinction clear in the button's tooltip/label if there's any ambiguity risk (e.g. "Clear view" rather than a bare "Clear").

## Note on the selection widget and "nothing changed in the UI"
Both are expected at this stage, not bugs:
- The selection widget hasn't been touched yet — that's UX-2, which hasn't started. UX-1.1 was deliberately foundation-only (shared stylesheet + doc), not a visual pass.
- Once this regression sprint clears and the UI/UX initiative resumes, UX-1.2 (the visual audit) and then UX-2 (the actual selection widget rebuild) are where visible, felt changes start showing up. Worth setting that expectation explicitly so the next report isn't measuring UX-2-shaped progress against a UX-1.1-shaped commit.

---

## Testing expectations
- Every fix in this sprint needs a regression test that would have caught the specific bug, not just a general "the feature works" test — we already have those and they didn't catch these, so the gap is specifically in *this class* of bug (wiring breaks after refactor, layout breaks at scale).
- Final manual pass: on the actual large real instance (634 modules), click through every tab in sequence, confirm no shared/oversized scrolling; click the Addon Path Manager button and confirm it opens; do a general feel-check for lag across a few common actions.

## Report-back template
```
Ticket REG.1 (Addon Path Manager button): [done/blocked] — root cause found
Ticket REG.2 (Performance regression): [done/blocked] — measurements + root cause
Ticket REG.3 (Shared/blank scrollbar): [done/blocked] — root cause + patterns.md updated
Ticket REG.4 (Follow logs): [done/blocked] — precise failure mode + whether related to REG.1-3
Ticket REG.5 (Log Clear button): [done/blocked]
Manual E2E on real large instance: [pass/fail]
Deviations: ...
Open questions for PM: ...
```
