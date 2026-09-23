# Odoo Vite — Sprint 10 Spec: Developer Tools, Part 1 (Introspection)

**Depends on:** Sprint 9. **Why split from Part 2:** this half is read-oriented — querying Odoo's ORM/database for information — with a much lower blast radius than Part 2's live process control (interactive shell, file-watching auto-restart, running test suites). Shipping and stabilizing the lower-risk half first is the right sequencing, same reasoning as splitting Database Operations from Module Management earlier in this project.

---

## Design note: how do we talk to a running instance's ORM at all?
This is the first real architectural decision of this sprint, and it affects every ticket below — **decide it once, here, not separately per ticket.**

Two realistic approaches:
1. **`odoo-bin shell`** (already used once, Sprint 6's Uninstall Module ticket, via `button_immediate_uninstall`) — spawn a one-shot Python process with the Odoo environment loaded, script what you need, capture output, exit. Simple, reuses an established pattern, but each call pays process-startup cost (loading the full Odoo registry), which is fine for occasional actions (Uninstall) but potentially slow for something used repeatedly and interactively (Record Browser, especially).
2. **Odoo's own XML-RPC/JSON-RPC API** against a *running* instance (the instance must actually be started and serving) — faster for repeated calls since there's no cold-start registry load each time, but only works when the instance is running, whereas `odoo-bin shell` works regardless of running state.

**Recommendation, decide-and-confirm:** use XML-RPC/JSON-RPC against a running instance for anything interactive/repeated (Record Browser, Model Inspector, Cron Jobs — all naturally used while poking around a live instance), and reserve `odoo-bin shell`-style one-shot invocation for anything that's inherently a single action regardless of running state. If a ticket below needs to work against a **stopped** instance too, that's a real constraint — note it explicitly rather than silently only supporting the running case.

## Ticket 10.1 — Shared RPC client module
- `core/odoo_rpc.py` (new): a thin wrapper around Odoo's XML-RPC (or JSON-RPC if you find it meaningfully better suited — note which and why) endpoints — `authenticate()`, `execute_kw()` for generic model calls. This is the **one place** all of Part 1's tickets should route through, not three separate ad-hoc RPC implementations.
- Handle the obvious failure modes clearly: instance not running, wrong/stale credentials, database doesn't exist or isn't initialized (reuse `db_state.py` from Sprint 5 to check this before even attempting the RPC call, don't let a confusing RPC error be the first sign of an uninitialized database).

## Ticket 10.2 — Model Inspector
- `list_models(instance, db) -> Result` and `get_model_metadata(instance, db, model_name) -> Result` via `odoo_rpc.py`: fields (name, type, relation target if applicable, required/readonly/computed flags), methods aren't generally introspectable via RPC in a useful way for arbitrary Python methods — scope this realistically to what `ir.model.fields`, `ir.model.constraint`, and `ir.model.access` actually expose (fields, SQL/Python constraints, access rules per group), not literal Python method signatures unless you find a clean way to get those too.
- UI: new "Dev Tools" tab (or sub-section) on the instance detail page, model picker (searchable, since there can be hundreds of models) → metadata view (fields table, constraints, access rules).

## Ticket 10.3 — Record Browser
- Built on `odoo_rpc.py` + the model list from 10.2. Given a selected model: list records (paginated — models can have huge record counts, don't fetch everything at once), search/filter (basic domain-building UI, doesn't need to support every possible Odoo domain operator in v1 — start with common ones: equals, contains, and a few comparison operators, note what's covered), and CRUD (create/update/delete a record).
- **This is the highest-risk ticket in Part 1** despite being "just CRUD" — it's a generic tool that can modify/delete arbitrary production-looking data with no domain-specific guardrails. At minimum: require the same type-to-confirm pattern already established for other destructive actions (Remove Instance, Drop Database) on **delete**, and show a clear diff/preview before committing an **update**. Flag to PM if you think this needs an even stronger safety net (e.g. restricting delete to non-system models, or requiring a confirmation phrase mentioning the model+record) — I'd rather over-guard this one than under-guard it.

## Ticket 10.4 — Cron Jobs
- `list_cron_jobs(instance, db) -> Result` via `odoo_rpc.py` querying `ir.cron`: name, active state, next execution time, interval, the model/method it calls.
- UI: simple table on the Dev Tools tab. Nice-to-have if straightforward: an "Run Now" trigger action per cron job (reuses `execute_kw` to call the cron's method directly) — note if you build this, since it's not explicitly in the feature spec but fits naturally; skip it if it adds meaningful complexity, this isn't required for the ticket to be done.

## Ticket 10.5 — Generate launch.json
- Pure file-generation, no RPC needed — genuinely independent of everything else in this sprint, can be built in parallel/first if convenient.
- `core/devtools_export.py`: generate a VS Code/Cursor `.vscode/launch.json` debugpy-attach configuration for a given instance — needs the venv's python path, the instance's `community_path`, the conf path, and the port `debugpy` would listen on (this requires the instance's `odoo-bin` invocation to actually be launched with `debugpy` support for attach-mode to work — note whether that's something we also need to wire into `process_manager.py`'s launch command, e.g. an optional "launch with debug support" toggle, or whether this ticket is purely file-generation assuming the user wires that up themselves; clarify which and document it in the generated file's comments if it's the latter).
- UI: "Generate launch.json" button, writes into the instance's folder (or offers a save-location picker), confirms success with the file path shown.

## Ticket 10.6 — Open in VS Code / Cursor
- Detect installed editors (check for `code`/`cursor` on `PATH`, similar in spirit to the existing system-requirements detection pattern from Sprint 1 — reuse that detection style, don't build a separate one).
- "Open in VS Code" / "Open in Cursor" buttons (show only whichever is actually detected as installed) that shell out to `code <instance_path>` / `cursor <instance_path>`.
- **Test:** confirm this actually opens the folder on a system with VS Code installed; confirm a clean, non-crashing message when neither is installed rather than a raw failed-subprocess error.

---

## Testing expectations
- `pytest` for `odoo_rpc.py` (mocked RPC responses: successful call, auth failure, connection-refused/not-running, confirm each maps to a clear `Result` message).
- `pytest` for Model Inspector/Cron Jobs' data shaping (given mocked RPC results, confirm the UI-facing structure is correct).
- Manual E2E: inspect a real model's metadata on a running instance and confirm it matches what you'd see in Odoo's own Technical Settings; browse/search/create/update/delete a test record via Record Browser (use a throwaway record, not something that matters) and confirm each operation's confirmation/preview actually appears before committing; list cron jobs and confirm next-run times look sane; generate a launch.json and confirm its paths are correct; open an instance folder in whichever editor you have installed.

## Out of scope for Sprint 10
- Odoo Shell (interactive), Dev Mode Watch, Run Tests — Sprint 11 (Part 2)

## Report-back template
```
Ticket 10.1 (odoo_rpc.py shared client, XML-RPC vs JSON-RPC choice): [done/blocked]
Ticket 10.2 (Model Inspector): [done/blocked] — scope of what's introspectable
Ticket 10.3 (Record Browser): [done/blocked] — safety-net approach for delete/update
Ticket 10.4 (Cron Jobs): [done/blocked] + Run Now built or skipped
Ticket 10.5 (Generate launch.json): [done/blocked] — debugpy wiring question answered
Ticket 10.6 (Open in VS Code/Cursor): [done/blocked]
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
