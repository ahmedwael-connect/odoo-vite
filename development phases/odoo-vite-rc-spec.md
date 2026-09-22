# Odoo Vite — Release Candidate Sprint: Integration Testing & Stabilization

**Depends on:** Sprints 1–4 + Phase 1.5 (all accepted). This sprint adds **no new features**. Its only outputs are: a completed test matrix with pass/fail evidence, bug-fix tickets opened and closed inline as issues are found, and a go/no-go recommendation back to PM.

**Philosophy for this sprint:** every prior E2E pass tested one feature in isolation on a clean environment. This sprint's job is to find where features **interact badly**, and where the app behaves under **non-ideal conditions** — not to re-prove things that already passed. If a test in this matrix passes cleanly, move on; don't pad the report. If something fails, open a numbered bug ticket, fix it, retest, and note it in the report — fixes happen inside this sprint, they don't get deferred to a backlog unless they're clearly out-of-scope architectural work (flag those to PM instead of silently punting).

---

## 1. Cross-feature integration matrix

Run these in order — each builds on the previous instance's state, mimicking a real user's session rather than resetting to clean state every time (that's the point: find seam bugs).

| # | Scenario | What to verify |
|---|---|---|
| 1 | Create instance in **managed** mode (H.1) | Role has no CREATEDB, wizard review correctly shows mode, instance provisions successfully |
| 2 | First Start on that instance | H.1's explicit create-database-at-moment-of-need path fires correctly (not the old implicit path), confirm dialog shows accurate command, db_created flips correctly |
| 3 | Set an enterprise addons folder from a **different** major version than the instance (H.5) | Mismatch badge appears at set-time AND persists on detail page after navigating away and back |
| 4 | Attempt instance creation with **no keyring available** (simulate by disabling Secret Service or testing on a session without one) | H.2's abort-before-row-exists behavior fires; no plaintext row is silently created; opt-out checkbox works if deliberately chosen; audit log records the opt-out |
| 5 | Track 2–3 additional databases on the managed instance (Sprint 4) | Discover + manual track both work; idempotent (re-track same db doesn't duplicate) |
| 6 | Switch Database to a tracked-but-not-yet-created db while instance is running | H.1's create-at-moment-of-need + Sprint 3's confirm/collision logic both fire correctly together (this is the exact seam most likely to have a bug — H.1 and Sprint 3's flows were built in different sprints) |
| 7 | Remove that instance, checking **all** tracked DBs in the dialog (H.3) | Every checked db is actually dropped, none unchecked are touched, stop→drop→rmtree→row order holds, audit log has full detail |
| 8 | Adopt an existing (non-Odoo-Vite-created) Odoo install, conf missing 2–3 optional fields | Lenient validation correctly identifies missing vs present; gaps are fillable; adopted instance's live status correctly reflects whether it's actually running; **critically**: confirm adopted mode never triggers any H.1 managed-mode role creation logic (adopt must never touch roles/privileges at all) |
| 9 | Remove the adopted instance | Confirmed zero file/db changes (diff the folder contents before/after — literally checksum or `ls -la` before and after, don't just trust the UI said so) |
| 10 | Create a **second** managed instance reusing the **same port** as an existing one | Correctly rejected at form-validation time (Sprint 2), not discovered only at Start |
| 11 | Create a **second** instance with the same instance name (case-different, e.g. "Test" vs "test") | Decide/verify: is name uniqueness case-sensitive or not? If this wasn't explicitly decided before, decide now and document it — don't leave it ambiguous |

## 2. Adverse-condition tests

| # | Scenario | What to verify |
|---|---|---|
| 12 | Kill Postgres (`sudo systemctl stop postgresql`) then attempt to Start an instance | Clean, readable error — not a raw traceback, not an app crash. Instance correctly shows `"error"` status afterward |
| 13 | Restart Postgres, then retry Start on that same instance | Recovers cleanly without needing to recreate/re-adopt the instance |
| 14 | Fill the disk (or simulate via a small quota/tmpfs) during a **clone** operation | Sprint 2's cancel/discard path handles this gracefully — no orphaned partial folder left with no registry trace, no orphaned registry row with no folder |
| 15 | Kill the Odoo Vite app itself (not the odoo-bin process) while an instance is running, then reopen the app | `get_statuses()` correctly re-discovers the still-running process and PID on next launch (registry state survives app restart, this hasn't been explicitly tested before) |
| 16 | `kill -9` the odoo-bin process externally while Odoo Vite is closed, then reopen the app | Correctly detected as stopped/process_gone on next launch, not shown as falsely "running" |
| 17 | Attempt to Start an instance whose `community_path` folder has been manually deleted outside the app (simulate user error) | Clear, specific error message identifying what's missing — not a generic failure |
| 18 | Run two Start attempts on the same instance in quick succession (double-click Start button) | Already-running guard (Sprint 3) prevents a second process from launching; no race condition spawning two odoo-bin processes on the same port |

## 3. Data integrity checks

| # | Scenario | What to verify |
|---|---|---|
| 19 | Inspect `~/.local/share/odoo-vite/odoo_vite.db` directly with a sqlite browser after the above scenarios | No orphaned rows referencing deleted paths, no NULL where a required field should exist, `tracked_dbs`/`auto_update_modules` JSON columns are well-formed in every row |
| 20 | Inspect `audit.log` after the above scenarios | Every destructive/state-changing action (create, start, stop, remove, db create/drop, plaintext opt-out) has a corresponding line; no obviously missing gaps |
| 21 | Confirm no plaintext passwords exist in the SQLite file for any instance that went through the keyring path (grep the raw db file for a known test password string) | Passwords are genuinely not recoverable from the SQLite file itself when keyring was used |

## 4. UX/polish pass (lightweight — not the focus of this sprint, but note anything glaring)
- Does every long-running action (clone, pip install, start, provisioning) have a visible loading/progress indicator? Anything that can silently hang for >2s with no feedback is worth a quick note, even if not fixed in this sprint.
- Do all destructive actions require the confirmation level we specified (type-to-confirm for managed remove, lighter for adopted)? Spot-check, don't re-audit every dialog from scratch.

## 5. Bug ticket format (use for anything found)
```
BUG-<n>: <short title>
Found in: scenario #<n> above
Severity: blocker / major / minor
Repro: ...
Root cause: ...
Fix: ...
Retest result: pass/fail
```

## 6. Report-back template
```
Integration matrix (1-11): [n/11 passed clean, n bugs found+fixed, n deferred]
Adverse-condition tests (12-18): [n/7 passed clean, n bugs found+fixed, n deferred]
Data integrity checks (19-21): [pass/fail summary]
UX/polish notes: ...
Bug tickets: [list using format above, however many were found]
Deferred items (with PM justification for why not fixed now): ...
Overall recommendation: [ready for release / needs another RC pass / specific blockers]
```

---

## PM note
Be honest in this report, including if the recommendation is "not ready." A clean-sounding report that hides friction is worse than a report with a real bug list — that's the entire point of running this sprint before a release.
