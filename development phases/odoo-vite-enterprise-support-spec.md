# Odoo Vite — Sprint: Enterprise Edition Support

**Can proceed now, in parallel with UX-1.2/UX-2** — minimal selection-widget/list-UI surface, doesn't conflict with the current UX-work-blocks-new-list-UI decision.

**Relevant history to build on, don't re-derive from scratch:** the app already has partial enterprise awareness — `enterprise_path` on the instance record, the version-mismatch badge from Phase 1.5/Sprint 5 (compares enterprise manifest version against instance version), and the explicit project rule that Odoo Vite **never clones or vendors Enterprise code itself** (it's the user's own licensed checkout — folder-picker only, established early in this project and still correct). This sprint formalizes and extends that existing partial support into the three requested features, it doesn't start from zero.

---

## Ticket ENT.1 — Detect Enterprise
- `core/enterprise.py` (new, or extend wherever `enterprise_path` logic already lives — check first, don't create a second home for the same concept): `detect_enterprise(instance) -> Result` — determine whether an instance is actually running Enterprise, not just whether a folder is configured. Two signals worth combining: (a) `enterprise_path` is set and non-empty in the registry, (b) the path actually contains recognizable Enterprise module structure (reuse whatever validation already exists from the Create/Adopt wizards' enterprise-folder check).
- Report a clear tri-state, not just true/false: "Enterprise configured and looks valid," "Enterprise path set but doesn't look valid" (reuse the existing warning pattern), "Community only, no Enterprise configured."
- UI: this becomes the data source for a clear Enterprise/Community badge on the instance overview (mentioned as a "nice to have" back in the original Enterprise discussion, never actually built — do it now, it's cheap and this ticket produces the exact data it needs).

## Ticket ENT.2 — Load Enterprise
- "Clone the enterprise repository at a specified branch and wire it into" the instance, per the feature table. **Important scope clarification, consistent with this project's standing rule:** Enterprise is a private, licensed repository — the user must already have their own GitHub access to it. This ticket should **not** attempt to clone it using credentials we don't have or shouldn't be handling. What this actually means in our context: given a **user-provided** git URL (their own authenticated remote, or an SSH-configured one already working on their machine) and a branch, run the clone the same way `git_manager.clone_instance()` already does for community (reuse that pattern, don't write a second cloner) into a location under the instance's folder, then set `enterprise_path` and update `addons_path` (reuse `addon_paths.py`'s add-a-path logic from Sprint 7, don't build a third way to add a path to the derived string).
- If the user doesn't have working git credentials for Enterprise configured, the clone will simply fail the normal git way (auth error) — surface that clearly, don't try to work around it or prompt for credentials ourselves; this is intentionally staying out of credential management, consistent with the earlier decision not to build Enterprise auth into the wizard.
- UI: a small form (branch input, defaulting to the instance's own Odoo version as a sensible starting guess) + confirm, reusing the clone-progress-log pattern already established (Sprint 2).

## Ticket ENT.3 — Unload Enterprise
- "Remove enterprise from `addons_path` (keeps clone on disk)" — this is explicitly non-destructive to the actual files, matching the project's established "never touch licensed third-party code destructively" posture. Implementation: remove/disable the enterprise path entry via `addon_paths.py` (prefer **disable** over hard-remove from the structured list, consistent with the whole reason Enable/Disable exists — the user may want to re-enable it later without re-cloning) and clear/update `enterprise_path` on the instance record as appropriate, but leave the actual cloned folder on disk untouched.
- UI: a clear "Unload Enterprise" action (Overview or wherever the new badge from ENT.1 lives) with a brief confirmation explaining exactly what will and won't happen ("this instance will stop using Enterprise addons; the files themselves are not deleted").

---

## Testing expectations
- `pytest` for `detect_enterprise()`'s tri-state logic (mocked folder states: valid, present-but-invalid, absent).
- Manual E2E: detect on an instance that already has a valid enterprise folder configured (reuse `odoo_19_adopted` or similar from existing testing) and confirm the badge is correct; Load on a fresh instance using a real accessible git remote if you have one available for testing (even a dummy/test repo standing in for "enterprise" structurally is fine for proving the mechanism, note if you used a stand-in); Unload and confirm the addons_path entry is gone/disabled but the cloned folder is still present on disk.

## Report-back template
```
Ticket ENT.1 (Detect Enterprise + badge): [done/blocked]
Ticket ENT.2 (Load Enterprise): [done/blocked] — credential-failure handling confirmed
Ticket ENT.3 (Unload Enterprise): [done/blocked] — confirmed non-destructive to files
Manual E2E: ...
Deviations: ...
Open questions for PM: ...
```
