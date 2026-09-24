# Odoo Vite — Fix Round 2: Addon Manager Enable/Disable + Follow Logs

**Why this is separate and comes first:** both of these were previously reported as working/not-reproducible, and direct evidence (a screenshot, in the addon manager's case) contradicts that. Before any new feature work, these need to actually be confirmed fixed by a standard stronger than "I ran a live check and it worked" — see Acceptance Criteria at the bottom, read it before starting.

---

## Ticket F2.1 — Addon Path Manager: Enable/Disable is missing or hidden
- **Check the hypothesis first:** the reported "dialog too small, not showing good data" and "no enable/disable" may be the same bug — a fixed-width dialog clipping a control off past its visible boundary, same class as H-M1. Before writing any new code, check whether an Enable/Disable button/toggle **exists in the widget tree** for each row but isn't rendering within the visible dialog bounds (e.g. a 5th button in a row that only has room for 4, or a toggle that's present but zero-width/off-canvas). Use a widget inspector (GTK Inspector, `GTK_DEBUG=interactive`) rather than just reading the code — if the code constructs the widget but it's not visible, that confirms the clipping hypothesis directly.
- **If the hypothesis is confirmed** (control exists, is clipped): fix by making the dialog properly resizable with a sane default size that fits the actual content (per Ticket F2.2 below — this and F2.2 likely share one fix), not by cramming more into a fixed width.
- **If the hypothesis is wrong** (control genuinely isn't there — e.g. it got dropped during the REG.3 or earlier consolidation and was never actually re-added despite the Sprint 9 report): implement it for real this time — a per-row toggle (switch or checkbox) that excludes the path from the derived `addons_path` string without removing it from the list (this data-layer behavior was reportedly already correct — `addon_paths.py`'s enabled/disabled state — so this may only need the **UI control** reconnected to already-working backend logic, check that before assuming backend work is needed too).
- **Required before marking this done:** a description of exactly where the Enable/Disable control is in the UI (which row, what it looks like, what it's labeled) plus confirmation that toggling it and clicking Apply changes measures in `addons_path` — not a description of the backend function being called successfully, the actual rendered control being clicked.

## Ticket F2.2 — Addon Path Manager: dialog sizing
- Make the dialog properly resizable (per H-M1's original fix pattern — set a sane `default_width`/`default_height` that fits the actual row content without truncation at that default size, and don't disable `resizable`).
- Full paths are currently truncated to something like `/home/ah…oo/addons` with no way to see the rest — either widen the default dialog size enough that realistic paths fit without truncation, or if truncation is unavoidable for very long paths, add a tooltip on hover showing the full path (cheap, doesn't require resizing to arbitrary widths for edge-case-long paths).
- **Test:** open the dialog with an instance that has several long real paths (reuse the same `odoo_19_adopted` test instance from your other testing), confirm paths are readable without needing to resize, and confirm the dialog *can* be resized if the user wants more room.

## Ticket F2.3 — Follow logs: rigorous re-investigation
- Your prior check appended a marker line directly to the log file and confirmed it appeared — that's a valid check of the *tailing* mechanism, but it may not represent what the user is actually doing when they say "follow isn't working." Before concluding "not reproduced" again, walk through the **actual UI interaction path** a user would take: open the Logs tab on a real running instance (not just a static file), let Odoo itself produce new log lines through normal operation (not an artificially appended marker), watch whether the view scrolls to show them without the user touching anything. Also specifically test: does Follow correctly resume after REG.3's scroll-isolation fix landed — it's plausible the per-tab scroll fix changed something about how the Logs tab's scroll position/adjustment behaves, and this is worth checking as a possible side effect of that fix rather than assuming it's unrelated.
- If it still doesn't reproduce after this more realistic walkthrough, report exactly what you did, step by step, so if the user tries again and it still fails for them, we have a precise basis to compare against what they're doing differently.

---

## Acceptance criteria for this whole fix round
Given the history here, "done" on these two tickets specifically requires: a precise description of the exact UI control (location, label, appearance) that was clicked/toggled, and the specific before/after state observed (not just "it worked"). This is a higher bar than usual, deliberately, because the usual bar has now let contradicted claims through more than once on this exact feature area.

## Report-back template
```
Ticket F2.1 (Addon Enable/Disable): [done/blocked] — clipping hypothesis
  confirmed or ruled out, exact control location/behavior described
Ticket F2.2 (dialog sizing): [done/blocked]
Ticket F2.3 (Follow logs re-investigation): [done/blocked] — exact steps taken
Deviations: ...
Open questions for PM: ...
```
