# Odoo Vite — Sprint 6 Spec: Polish Fixes + Module Management Phase

**Depends on:** Sprints 1–5 + all hardening/RC/hotfix sprints (accepted). This sprint has two parts: **Part A** (small, fix first) and **Part B** (the new Module Management feature set, the bulk of this sprint).

---

## Part A — Polish fixes (do these first, they're small)

### Ticket A.1 — Discover Databases: stop pre-selecting everything
- **Bug:** every checkbox in the Discover dialog currently starts checked, including databases in the "Other Odoo" and "Uninitialized" groups — meaning the grouping work from Sprint 5 is being defeated by the default state forcing the user to manually uncheck most of the list.
- **Fix:** default checkbox state should be **checked only for the "Likely this version" group**, unchecked for "Other Odoo databases" and "Uninitialized/non-Odoo" (those groups stay visible and available, per the "nothing is hidden" principle — just not pre-selected). This matches what a user actually wants most of the time and keeps the manual-select case easy (they can still tick more).
- **Test:** open Discover against a mix of matching/mismatched/uninitialized databases, confirm only the matching group arrives pre-checked.

### Ticket A.2 — Discover Databases: fix checkbox visual state
- **Bug reported:** it's unclear which rows are selected vs not without hovering — the checked/unchecked visual states aren't distinct enough.
- **Fix:** give checked and unchecked rows a clearly different, always-visible (not hover-dependent) treatment — e.g. a filled/colored checkbox icon vs an outline one, or a distinct row background for checked rows that doesn't rely on `:hover` state. Use whatever GTK4/libadwaita's standard `Gtk.CheckButton` styling provides if it's suffient once wired correctly — this may simply be a bug in how the checkbox widget is bound to state rather than something needing custom CSS; investigate root cause before reaching for a style override.
- **Test:** visually confirm selected vs unselected rows are distinguishable without hovering, in both light and dark theme if the app supports both.

### Ticket A.3 — Show app version in the main toolbar
- Add the app's version string to the left side of the main header bar (`Adw.HeaderBar`), small and unobtrusive — e.g. next to or below the "Odoo Vite" title, or as a subtitle.
- Source the version from a single place (e.g. a `__version__` constant in `main.py` or a small `core/version.py`) so it's one line to bump on future releases, not scattered across files.
- **Test:** confirm it's visible and reads correctly; confirm bumping the one constant updates it everywhere it's shown (just the one place, for now).

---

## Part B — Module Management Phase

### Design note before starting
Several of these features (List, Diff, Dependency Graph) need to read Odoo's module state either via direct Postgres queries against `ir_module_module` or by talking to a running instance. **Decide and document which approach each feature uses, and be consistent**: a database query works whether the instance is running or not (good for List/Diff), while Install/Update/Uninstall inherently require actually running Odoo (either a live running instance, or a one-shot `odoo-bin` invocation against a stopped one, which is how Odoo itself handles `-i`/`-u`). Don't build a feature that silently requires "must be running" without the UI making that obvious.

### Ticket B.1 — List Modules
- New "Modules" tab on the instance detail page (alongside the existing "Databases" tab from Sprint 5).
- `core/module_manager.py`: `list_modules(instance, db_name) -> Result` — query `ir_module_module` (`name`, `state`, `installed_version`, `latest_version`, `summary`/`shortdesc`) for the selected database. Requires the database to be `initialized` (reuse `db_state.py` from Sprint 5 — if not initialized, show a clear "database not initialized" message instead of an empty/broken table, don't guess).
- UI: table with search box (filter by name/summary) and a state filter (Installed / Upgradeable / Installable / All) — mirrors the Discover dialog's search+filter pattern already established, reuse that component if feasible rather than building a second one.

### Ticket B.2 — Install Modules
- `module_manager.install_modules(instance, db_name, module_names: list[str], progress_cb) -> Result` — runs `odoo-bin -c <conf> -d <db> -i <comma_joined> --stop-after-init`, streamed via the existing `core/proc.py` streaming helper (Sprint 2/3's shared pattern — reuse, don't reimplement). Must refuse (with a clear message) if the instance is currently running against a **different** database than the target, since Odoo can't have two processes safely writing to the same filesystem/venv concurrently in conflicting ways — confirm the actual constraint here and handle it correctly rather than assuming.
- UI: from the List Modules table, multi-select "Installable" modules + an "Install Selected" button, confirmation showing the exact command (same pattern as the Start confirmation dialog), progress log pane.

### Ticket B.3 — Update Modules
- This is explicitly the "full pipeline" feature, per spec: git pull (on `community_path`, and `enterprise_path` if the user has their own update mechanism for it — note: we likely **cannot** `git pull` an enterprise checkout ourselves the way we do community, since it's the user's own clone with their own credentials; confirm this assumption and if correct, scope Update Modules' git step to community only, documented clearly in the UI so the user knows enterprise isn't auto-updated by this action), then `pip install -r requirements.txt` (re-run in case the update added dependencies), then `odoo-bin -u <modules> --stop-after-init`.
- Each of the three stages should be individually visible in the progress UI (reuse the multi-stage progress pattern from the original provisioning wizard, Sprint 2) — a failure in stage 2 (pip) shouldn't look identical to a failure in stage 3 (module update) in the log.
- This is a meaningfully riskier operation than Install (it changes code on disk, not just database state) — require an explicit confirmation with a clear warning, and consider whether a backup-prompt ("back up the database before updating?" — linking to Sprint 5's Backup feature) belongs here. Flag this design question back to PM if you think it should be mandatory rather than optional.

### Ticket B.4 — Uninstall Module
- Per spec: "via the Odoo shell." Investigate `odoo-bin shell` — this runs a Python shell with the Odoo environment loaded, letting us script `module.button_immediate_uninstall()` or equivalent programmatically, rather than needing a full web UI interaction. Confirm the correct API call for the target Odoo version range (15.0–18.0/19.0) since internal method names occasionally shift between versions — note in your report if this needs version-specific handling.
- UI: "Uninstall" action per installed module row, confirmation (uninstalling can cascade to dependent modules — Odoo will refuse or warn on its own side if there are blocking dependents; surface whatever Odoo itself reports, don't try to pre-compute the dependency impact ourselves for this ticket, that's B.6's job).

### Ticket B.5 — Module Diff
- `module_manager.diff_modules(instance, db_name) -> Result` — for each installed module, compare the database's recorded `installed_version` against the `version` key in that module's on-disk `__manifest__.py` (search `addons_path` for the module's manifest). Report: in sync / disk is newer (needs update) / db is newer or manifest missing (unexpected — flag clearly, don't guess why).
- UI: table or badge overlay on the List Modules view — this is a diagnostic lens on data B.1 already fetches, not a separate screen, keep it lightweight.

### Ticket B.6 — Module Dependency Graph
- `module_manager.get_dependency_graph(instance, db_name) -> Result` — build a graph from `ir_module_module_dependency` (or the manifest `depends` key if querying disk instead of db — pick whichever is more reliable/available and note which).
- UI: this is the one place in the app so far that needs a genuinely interactive visual, not a table — use d3 (per the feature spec) inside a `WebKitGtk`/`Gtk.WebView` widget-embedded HTML+JS view if that's available in this environment, or note clearly if it isn't and propose the closest workable alternative (e.g. a static but zoomable/pannable rendering). **Flag to PM before building** if `WebKitGtk` isn't a clean fit for a native GTK4 app on Ubuntu — this is a real architecture decision, not a small implementation detail, and I'd rather decide it deliberately than have it decided implicitly by whatever's easiest.

### Ticket B.7 — Scaffold Module
- This is the most self-contained, least risky feature in this batch — pure code generation, doesn't touch a live instance or database at all.
- `core/module_scaffolder.py`: given a declarative definition (start with a reasonably simple input shape — module name, models with fields, whether it needs views/menus/security/tests/controllers/demo data as toggles — don't over-engineer the input format in v1, a structured form is fine, a full DSL is not needed yet), generate a complete Odoo module folder: `__manifest__.py`, `__init__.py`, `models/`, `views/`, `security/ir.model.access.csv`, `controllers/` (if requested), `tests/` (if requested), `demo/` (if requested) — following standard Odoo module conventions for the target version.
- UI: a form-based wizard (reuse the existing wizard shell pattern), output written directly into the instance's `custom_addons` folder so it's immediately visible to that instance.
- **Test:** generate a scaffold, then actually attempt to install it via B.2 against a real instance, confirming Odoo accepts the generated module structure without errors — this is the real acceptance test for this ticket, a folder that "looks right" but doesn't actually load in Odoo doesn't count as done.

---

## Sequencing note
Given the size of Part B, I'd expect this to likely span more than one sprint's worth of work in practice. **Ship and report Part A first, standalone**, since it's small and the fixes are needed regardless of how Part B goes. For Part B, tackle in the order listed — B.1 (List) is a prerequisite for B.5 (Diff) and useful scaffolding for B.2–B.4's UI, and B.7 (Scaffold) can be built in parallel since it's fully independent of the rest. If B.6 (Dependency Graph) turns into a bigger architecture question than expected, it's fine to report the rest done and flag B.6 as needing a follow-up decision from PM rather than blocking everything else on it.

## Report-back template
```
Part A.1 (Discover default-selection fix): [done/blocked]
Part A.2 (Discover checkbox visual state): [done/blocked]
Part A.3 (app version in toolbar): [done/blocked]
Ticket B.1 (List Modules): [done/blocked]
Ticket B.2 (Install Modules): [done/blocked]
Ticket B.3 (Update Modules): [done/blocked] + backup-before-update question
Ticket B.4 (Uninstall Module): [done/blocked] + version-specific API notes
Ticket B.5 (Module Diff): [done/blocked]
Ticket B.6 (Dependency Graph): [done/blocked/flagged-for-PM-decision]
Ticket B.7 (Scaffold Module): [done/blocked] + real-install test result
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
