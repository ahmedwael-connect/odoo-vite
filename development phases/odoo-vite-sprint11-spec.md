# Odoo Vite — Sprint 11 Spec: Developer Tools, Part 2 (Live Process Control)

**Depends on:** Sprint 10 (Part 1's `odoo_rpc.py` may be reusable here for parts of Odoo Shell). **Do not start this sprint until Sprint 10 is reported and reviewed** — unlike most of this project's sprints, which could reasonably overlap, this one's tickets (interactive shell, auto-restarting file watcher, running arbitrary test suites) are meaningfully higher-risk than Part 1's, and I want Part 1's RPC foundation proven stable first.

---

## Ticket 11.1 — Odoo Shell (interactive, via WebSocket)
- This is the most architecturally novel ticket in the whole project so far: an interactive Python REPL, exposed over WebSocket, backed by a running (or one-shot spawned) `odoo-bin shell` process.
- **Validate before connecting, per the feature spec:** confirm python/odoo-bin/conf/db are all in a good state (reuse `db_state.py`, the venv/python checks from earlier sprints, and conf validation from Sprint 7) *before* attempting to open the shell — fail with a clear specific reason, not a raw connection error, if any prerequisite is missing.
- **Architecture question to resolve before building, flag to PM if genuinely ambiguous once you're in it:** a GTK4 desktop app doesn't typically need a WebSocket server talking to itself — the feature spec's WebSocket framing sounds like it's describing a web-app architecture (browser frontend ↔ backend shell process). Confirm whether the actual goal is simpler in our context: spawn `odoo-bin shell` as a subprocess and pipe stdin/stdout directly to a terminal-like widget in the GTK app (no WebSocket needed at all, this app already handles subprocess stdin per Sprint 6's `stdin_text` addition to `proc.py`), versus a genuine requirement for WebSocket specifically (e.g. if there's a future plan for a remote/web-based companion view of this app that isn't in scope yet). I'd lean toward the simpler direct-subprocess approach unless there's a concrete reason for WebSocket — but this is your call to make with reasoning, not mine to dictate blind, since you're the one who'll hit the actual implementation constraints.
- UI: a terminal-style widget (`Gtk.TextView` configured for monospace/terminal-like input+output, or investigate if GTK4/libadwaita has anything more purpose-built) on the Dev Tools tab, showing shell output and accepting input.

## Ticket 11.2 — Dev Mode (Watch)
- Launch an instance with a file watcher on `.py`/`.xml`/`.js`/`.css` changes under its `custom_addons` (and reasonably, the community/enterprise addon folders too, though those change less often during active development — confirm scope) that triggers an automatic restart when a change is detected.
- Use a real filesystem-watching mechanism (`inotify` via a library like `watchdog`, or GTK's own `Gio.FileMonitor` if that's a cleaner fit given we're already GTK-native — note which you chose and why) rather than polling the filesystem repeatedly.
- **Debounce restarts** — a save-heavy editor or a bulk file operation (like a git checkout) can trigger many change events in a burst; don't restart the instance once per file, batch changes over a short window (e.g. 500ms-1s of quiet) before triggering a single restart. Reuse the debounced-batching pattern already established for the Event Log Panel (Sprint 8, B.6) if applicable.
- UI: a "Dev Mode" toggle on the instance detail page (Overview, likely) — when on, restarts happen automatically and should be visibly logged (Event Log Panel is a natural fit) so the user isn't confused by an instance restarting without them clicking Restart themselves.

## Ticket 11.3 — Run Tests
- Execute an instance's module test suite against a (likely disposable/test) database — `odoo-bin -d <db> --test-enable -i <module> --stop-after-init` or the equivalent test-invocation pattern for the target Odoo version (confirm the exact flag set is consistent across 15.0–18.0/19.0, note any version differences found).
- **This should default to a non-primary database**, or at minimum warn clearly if run against what's currently set as primary — running tests can create/modify/destroy data in ways that are fine for a throwaway test db and bad for a real one. Consider whether this ticket should offer to spin up a fresh disposable test database automatically (reusing Sprint 5's Initialize Database) rather than making the user always pick one manually — flag your take on this to PM, it affects the UX meaningfully.
- UI: module/test picker, "Run Tests" button, streamed progress/output (reuse `proc.py`'s streaming pattern), pass/fail summary at the end.

---

## Testing expectations
- `pytest` where mockable: file-watcher debounce logic (simulate a burst of change events, assert exactly one restart is triggered), test-invocation command construction (assert correct flags per version).
- Manual E2E: open an interactive shell session and run a trivial command (e.g. `env['res.users'].search([])`) confirming real output comes back; enable Dev Mode, make a file change in a custom addon, confirm exactly one automatic restart happens (not a restart-per-file if you save multiple files quickly); run a real module's test suite and confirm pass/fail output is accurate against a database you can verify independently.

## Out of scope for Sprint 11
- Nothing further planned beyond this — Developer Tools is the last requested phase as of this spec. Do not start new feature areas without a new request.

## Report-back template
```
Ticket 11.1 (Odoo Shell): [done/blocked] — WebSocket vs direct-subprocess decision + reasoning
Ticket 11.2 (Dev Mode Watch): [done/blocked] — watcher mechanism chosen, debounce proof
Ticket 11.3 (Run Tests): [done/blocked] — disposable-db approach taken
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
