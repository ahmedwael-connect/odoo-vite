# Odoo Vite — Sprint 8 Spec: Overflow Fix + Logging & Monitoring Phase

**Depends on:** Sprints 1–7 (Configuration Management pending your final human pass — this sprint's Part A fixes a bug found during that pass, Part B is new work that can proceed in parallel).

---

## Part A — Fix: Configuration tab + instance sidebar overflow (fix first, small)

### Ticket A.1 — Same root cause as H-M1, new location
- **Bug:** the Configuration tab's raw key list and the `addons_path` display value (screenshots 1–2) extend past the visible window with no wrapping, and the instance sidebar list is affected too. This is the exact same class of bug as Hotfix H-M1 (long unbroken strings — e.g. a full `addons_path` value or a long conf key list — blowing out a container's natural width instead of wrapping or ellipsizing). H-M1's fix (ellipsize + max-width) was applied to the wizard dialogs that existed at the time; the Configuration tab didn't exist yet, so it never got the same treatment.
- **Fix:** apply the same max-width/ellipsize/wrap pattern established in H-M1 to (a) the raw config key-value table on the Configuration tab, (b) the `addons_path` read-only display value on that same tab, and (c) audit the instance sidebar list for the same issue since it's also reported as overflowing.
- **Process fix, not just a code fix:** since this is the second time a long-string-overflow bug has shipped in a new screen after already being fixed once elsewhere, add a line to whatever internal checklist/pattern doc you're keeping (if none exists, start one) noting "any new text container showing a value that could be long — paths, conf values, names — must use the established max-width/ellipsize pattern from H-M1, check this before shipping a new screen." This is a cheap process guard against a bug class that's now shown up twice.
- **Test:** verify on the same smaller/laptop-resolution display H-M1 was originally tested on (per that ticket's own note about testing at realistic resolutions, not just the dev's main monitor).

### Confirmed, no action needed
- Addon Path Manager dialog (screenshot 3) matches the requested design exactly — standalone wizard-style window, add/position/enable-disable/reorder/remove all present. No ticket needed.

---

## Part B — Logging & Monitoring Phase

### Design note before starting
This phase introduces **two genuinely new technical capabilities** the app hasn't needed before: (1) tailing a growing file efficiently without re-reading it from the start on every poll, and (2) running an external profiler (`py-spy`) against another process, which needs elevated permissions similar to what we already handle via `pkexec` elsewhere. Treat both as real design decisions, not just "add a feature" tickets — flag anything non-obvious back to PM rather than guessing, same standing rule as always.

### Ticket B.1 — Live Log Tail
- `core/log_tail.py` (new): seek-based tailing — track the last-read byte offset per instance's `log_path`, on each poll `seek()` to that offset and read only new bytes, rather than re-reading the whole file. Handle log rotation/truncation gracefully (if the file is smaller than the last known offset, it was rotated/truncated — reset to 0 and note this in the UI, don't crash or show garbage).
- UI: new "Logs" tab on the instance detail page. The feature spec mentions "virtualized rendering (react-window)" — that's a React/web concept and doesn't directly apply to a GTK4 app; the equivalent goal here is **don't render every line as a full widget if the log gets long** — use `Gtk.ListView` with a proper list model (which is already virtualized/recycling by design in GTK4, this is effectively "use the right built-in widget," not "build virtualization from scratch"). Confirm this approach with a genuinely large log file (tens of thousands of lines) before calling it done — a naive implementation that technically works on a small log but chokes on a big one doesn't count as done, same lesson as the Scaffold Module acceptance test.
- Auto-scroll to bottom on new lines, with a "paused" state if the user has scrolled up to read something (don't yank their scroll position while they're reading).

### Ticket B.2 — Log Search
- `log_tail.py` or a new `log_search.py`: search the full log file (not just the tailed/recent portion) with a regex pattern, optional level filter (parse Odoo's log level from each line's format), and optional time range (parse the timestamp prefix Odoo writes on each line). Return matches with a configurable number of context lines before/after (like `grep -C`).
- For large log files, don't load the whole file into memory for every search — stream/chunk-read it. Note your approach.
- UI: search bar + filter controls on the Logs tab, results shown either inline (jump-to-match in the tail view) or in a separate results list — pick whichever is simpler to get right, note your choice.

### Ticket B.3 — Log Issues (Doctor)
- `core/log_doctor.py` (new): scan a log file for a curated set of known failure signatures and report them in plain language, not just raw regex matches. Start with a reasonably small, high-confidence signature set rather than trying to cover everything at once: database connection failures, port-already-in-use, missing Python module errors (the exact class of bug we hit ourselves with `pkg_resources` — dogfood that one first, we know exactly what it looks like), permission-denied errors, and Odoo's own traceback markers as a generic "unhandled exception" catch-all when nothing more specific matches.
- Each detected issue should map to a short, actionable suggestion where possible (e.g. "port already in use" → suggest checking what else is using it), not just "here's a matching log line." If no specific suggestion applies, still surface the raw finding rather than staying silent.
- UI: "Run Doctor" button on the Logs tab, results as a simple list of findings with severity and suggestion text.

### Ticket B.4 — Slow Query Analysis
- Requires `pg_stat_statements` to be enabled on the Postgres instance — this is a Postgres-level extension that may or may not already be active. `core/db_manager.py` (extend): `check_pg_stat_statements_enabled() -> bool`, and if not enabled, the UI should say so clearly with instructions rather than silently showing an empty table (enabling it requires a Postgres config change + restart, which is arguably outside what we should do automatically via `pkexec` without explicit confirmation — flag this specific question to PM: should Odoo Vite offer to enable it for the user, or just detect-and-instruct? I'd lean detect-and-instruct given it requires a Postgres restart, which affects every instance sharing that Postgres server, not just one — confirm before building either path).
- If enabled: query `pg_stat_statements` filtered to the target database, show top N by total/mean execution time.

### Ticket B.5 — Flame Graph
- `core/profiler.py` (new): run `py-spy record -d <duration> -o <output.svg> --pid <pid>` against a running instance's process. `py-spy` attaching to another process typically needs elevated permissions on Linux (ptrace restrictions) — this is the same category of privilege need we already solved for system package installation via `pkexec`; reuse that established pattern rather than inventing a new privilege-escalation path.
- UI: "Profile" button on a running instance (Overview or a new dedicated spot — your call), duration selector (default a short, safe value like 10s — profiling for too long against a production-feeling instance is a real footgun, don't default to something long), progress indicator for the duration, then display/open the resulting SVG (an `Gtk.Picture`/simple viewer, or open in the system's default SVG viewer — note which).
- **Check `py-spy` is actually installed before offering this feature** — it's a third-party tool, not something we bundle. Extend the existing system-requirements-check pattern (Sprint 1) to detect it, and offer to install it the same way we already do for other prerequisites, rather than building a separate detection/install flow for just this one tool.

### Ticket B.6 — Event Log Panel
- This is different from B.1–B.3, which are about the *Odoo instance's* log file — this is an **in-app** event stream (status changes, pipeline steps like clone/provision progress, and optionally a merged view of tailed log lines) shown somewhere persistent in the app itself, not per-instance.
- Likely candidate: a collapsible panel (bottom of the main window, or a dedicated view accessible from the header) showing a running feed of app-level events — reuse the audit log (`core/audit.py`, Sprint 3) as the backing data source rather than building a second event-tracking system; this panel can simply be a live UI view over the same audit events already being recorded, with "debounced batching" (per the feature spec) meaning: don't re-render the panel on every single event if many arrive in a burst (e.g. during provisioning's verbose clone/pip output) — batch UI updates every ~200-300ms instead of per-event.

---

## Testing expectations
- `pytest` for `log_tail.py`'s seek/offset logic (including the rotation/truncation-detection case — write a test that simulates a file shrinking between polls), `log_search.py`'s regex+filter logic, and `log_doctor.py`'s signature matching (feed it real captured log snippets from bugs we've actually hit — the `pkg_resources` traceback is sitting right there in this project's own history, use it as a real fixture).
- Manual E2E: tail a real running instance's log and confirm new lines appear live; search for a known pattern and confirm context lines are correct; run Doctor against a deliberately broken instance (any of our own past bug repro scenarios work) and confirm it correctly identifies the issue; if `pg_stat_statements` can be enabled in your test environment, confirm Slow Query Analysis shows real data, otherwise confirm the detect-and-instruct path is clear; run a Flame Graph against a running instance and confirm the SVG is valid/openable; confirm the Event Log Panel updates live during a real provisioning run without freezing the UI under the burst of clone/pip output.

## Out of scope for Sprint 8
- Any new Database/Module/Configuration features — this sprint is Logging & Monitoring only, plus Part A's fix.

## Report-back template
```
Part A.1 (Configuration tab + sidebar overflow fix): [done/blocked]
Ticket B.1 (Live Log Tail): [done/blocked] — large-file test result
Ticket B.2 (Log Search): [done/blocked]
Ticket B.3 (Log Doctor): [done/blocked] — signature set covered
Ticket B.4 (Slow Query Analysis): [done/blocked] — pg_stat_statements question answered
Ticket B.5 (Flame Graph): [done/blocked] — py-spy detection/install handling
Ticket B.6 (Event Log Panel): [done/blocked]
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
