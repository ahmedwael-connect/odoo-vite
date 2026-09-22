# Odoo Vite — Hotfix Sprint: Real-User-Testing Findings

**Status change:** Release recommendation from the RC sprint is **rescinded**. Do not tag/ship v1.0.0 until this sprint is complete and PM re-confirms. Root cause: the RC sprint's test matrix was entirely agent-scripted; it never included a human clicking through the app cold. That gap let through bugs that only show up under real, unscripted use. This sprint fixes what was found; a human exploratory pass is now a **permanent required gate** before any future release sign-off, alongside the automated RC matrix — not a replacement for it, an addition to it.

**Depends on:** Sprints 1–4 + Phase 1.5 + RC sprint (all previously accepted; this sprint is corrective, not additive).

---

## Priority 1 — Blockers (nothing ships until these are fixed and retested)

### H-B1: `ModuleNotFoundError: No module named 'pkg_resources'` breaks every fresh Start
- **Root cause (confirmed by PM, verify on your end):** recent Python/pip no longer bundles `setuptools` into a fresh venv by default. `pkg_resources` lives inside `setuptools`, and Odoo's own module-loading code imports it directly (`odoo/modules/module.py`). Every instance created since venv creation stopped auto-including setuptools will hit this at `odoo-bin` startup, regardless of Odoo version.
- **Fix:** in `venv_manager.create_venv()` or immediately after, explicitly run `<venv>/bin/pip install --upgrade setuptools wheel` alongside the existing `pip install --upgrade pip`, **before** `install_requirements()` runs. Do this for every future instance.
- **Also required:** this bug already shipped into `odoo_15_test` and any other instance created during RC testing. Write a small **repair function** (`venv_manager.repair_venv(instance_id)`) that can be run against an already-provisioned instance to install the missing packages after the fact, and surface a "Repair venv" action on any instance whose Start fails with this specific error pattern — don't make the user delete and recreate the whole instance to fix a two-package gap.
- **Test:** fresh-provision a new instance end-to-end, confirm Start succeeds without this error, on at least two Odoo versions (not just 15.0 — this is a venv-layer bug, not version-specific, but confirm it doesn't interact oddly with any version-specific requirements.txt).

### H-B2: "Venv python missing at bin/python — re-run provisioning" false/premature error
- Investigate what check is producing this and why it fired on `odoo_19_adopted` (screenshot 3) — this looks like either (a) a path-existence check running before venv creation is actually flushed to disk, (b) a check looking for `bin/python` specifically when some environments only produce `bin/python3`, or (c) state left over from the H-B1 issue confusing a different check. Find the actual cause, don't guess-patch it.
- **Fix + test:** whatever the cause, add a regression test that would have caught it, and confirm the error no longer appears on a normal Start of a properly-provisioned instance.

### H-B3: Remove Instance fails with "database does not exist" despite the app believing it needs to drop it
- This is the same class of bug we already fixed once in the RC sprint (BUG-3: trusting a flag instead of ground truth) — it just wasn't applied to the Remove code path. `removal.py` needs to use the same `database_initialized()`/`exists()` ground-truth check that `start_instance`/`switch_database` already use, **before** attempting a drop, not just before create.
- Specifically: if the target database doesn't actually exist, Remove should treat that as "nothing to drop" and proceed cleanly (files + registry row), not abort the entire removal and leave everything intact. A missing database is not a reason to block removing the instance — log it and move on.
- **Test:** reproduce the exact reported scenario (instance whose DB was never successfully created, due to H-B1's crash happening mid-`-i base`) and confirm Remove now completes cleanly. Also retest the existing RC scenario #7 (removing an instance with real tracked databases present) to confirm this fix didn't regress the case where databases genuinely do exist and do need dropping.

---

## Priority 2 — Major (fix in this same sprint, not deferred)

### H-M1: Wizard/dialog windows are fixed-width and not resizable, can extend off-screen
- Audit every `Adw.Window`/`Gtk.Window`/dialog used across the New Instance wizard, Adopt wizard, and any modal dialogs (Discover Databases, confirm dialogs). Set sensible `default_width`/`default_height` and ensure `resizable` is not disabled anywhere it shouldn't be. Test specifically on a smaller/laptop-resolution display, not just whatever the dev's main monitor is — screenshot 1 shows the dialog exceeding the visible screen width entirely, which suggests it isn't respecting the working area at all.

### H-M2: Duplicate toolbar/headerbar in the creation wizard (screenshot 3)
- The provisioning progress window shows two stacked title bars ("New Instance — Step 6 of 6" appears twice). Likely an `Adw.ToolbarView`/`HeaderBar` nested incorrectly, or a leftover header from an earlier step not being torn down when the modal opens. Fix the widget tree so exactly one header renders.

### H-M3: No loading/busy feedback on Start, Stop, and Remove button clicks
- Every action-triggering button in the app (Start, Stop, Restart, Remove, Switch Now, Track, Create & Start) needs an immediate visual acknowledgment on click — a spinner, disabled-state, or busy cursor — before the background operation completes. This was implicitly expected since Sprint 1/3's spec talked about loading states for long operations, but evidently doesn't cover these specific buttons consistently. Treat this as a sweep across all action buttons, not a one-off patch — if you find one missing it, check the others.

---

## Priority 3 — Medium (fix if straightforward within this sprint; flag to PM if it needs its own ticket)

### H-P1: Discover Databases dialog usability
- Add a search/filter text entry at the top of the dialog (client-side filter on the already-fetched list, no new query needed).
- Cap the dialog's height and make the list scrollable — 17 checkboxes with no scroll affordance visible is not usable, and this will only get worse on systems with more databases.
- Not required this sprint, but note for backlog if not trivial: some indicator of which databases look plausibly related to this instance's version vs clearly unrelated (heuristic only, never filtering them out — just visual grouping/hinting) — this was raised as feedback but the original design intent was "show everything the user owns," which is still correct; don't silently start hiding entries.

---

## Testing expectations for this sprint
- Every Priority 1 fix needs both a regression test (pytest, mocked where appropriate) AND a live manual retest reproducing the original reported scenario exactly.
- Before reporting this sprint done, **do a second cold human-style pass yourself**: create a brand new instance from scratch through the full wizard, start it, stop it, switch its database, remove it — paying attention to anything that feels unpolished, not just whether it technically works. Note anything you notice even if you don't fix it this sprint — small papercuts, not just crashes.

## Report-back template
```
H-B1 (pkg_resources / setuptools missing): [done/blocked] + repair-function status
H-B2 (venv python false-missing error): [done/blocked] + root cause found
H-B3 (Remove: database-doesn't-exist handling): [done/blocked]
H-M1 (dialog resizing/off-screen): [done/blocked]
H-M2 (duplicate toolbar): [done/blocked]
H-M3 (loading feedback sweep): [done/blocked] + which buttons covered
H-P1 (Discover Databases search/scroll): [done/blocked]
Cold human-style pass notes: ...
Regression tests added: ...
Deviations: ...
Open questions for PM: ...
Recommendation: [ready to re-request release sign-off / not yet, here's why]
```
