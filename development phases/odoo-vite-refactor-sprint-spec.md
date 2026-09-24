# Odoo Vite — Refactor Sprint: UI Layer Decomposition

**Depends on:** all sprints through the v2.0.0 verification pass. **No new features in this sprint.** The only acceptable outcome is: same behavior, better-organized code, proven by tests + a real E2E pass. If a "small improvement while I'm in there" temptation comes up, resist it — mixing refactor and feature work is how regressions hide.

**Why now:** `window_main.py` is ~3,430 lines / ~85 methods on a single `MainWindow` class, handling every flow for every feature shipped since Sprint 3. `page_instance_detail.py` is at ~1,700 lines and trending the same way. This wasn't a single bad decision — it's seven feature phases of individually-reasonable additions with no structural checkpoint in between. This sprint is that checkpoint.

---

## Guardrails (read first)

1. **No behavior change is the success criterion.** Every existing test must still pass, unmodified in intent (imports/paths may need updating). Every manual scenario from the v2.0.0 verification checklist should still work identically afterward — re-run the relevant sections as your own regression check before reporting done, don't wait for another human pass to catch a refactor-introduced bug.
2. **Do this incrementally, not as one big-bang rewrite.** Move one feature area at a time, run the test suite after each move, commit each move separately. A refactor that's one giant commit is much harder to bisect if something breaks.
3. **Keep the public shape `MainWindow` exposes to the rest of the app stable** (signal connections, whatever `page_instance_detail.py`/wizards call back into) — this is an internal reorganization, not an API redesign. If you find the current internal API between `MainWindow` and its pages is itself awkward and worth changing, flag it as a separate observation, don't fold an API redesign into a "just move the code" sprint.

---

## Ticket R.1 — Establish the target structure
Before moving anything, decide and document the target module layout. My suggested starting shape (adjust if you find a better grouping once you're actually looking at the code's real coupling — you'll see the actual dependencies better than I can from outside):

```
odoo_vite/ui/
├── window_main.py          # slimmed: window shell, sidebar, header, status
                             #   polling, event panel, and composition of the
                             #   flow controllers below. No feature-specific
                             #   flow logic lives here anymore.
├── flows/
│   ├── __init__.py
│   ├── instance_lifecycle.py   # start/stop/restart/repair/remove/switch/
                                 #   set-primary/track/untrack/discover
│   ├── database_ops.py         # init/backup/restore/drop/validate
│   ├── module_ops.py           # list/install/update/uninstall/diff/deps/scaffold
│   ├── configuration.py        # conf read/edit/regenerate/addon paths/metadata
│   ├── logs_monitoring.py      # search/doctor/slow-query/profile/event panel wiring
│   └── dev_tools.py            # rpc/model-inspector/record-browser/cron/
                                 #   launch.json/editor-open/shell/dev-mode-watch/tests
```
- Each flow module is a class (e.g. `DatabaseOpsFlows`) instantiated once by `MainWindow` and given whatever shared context it needs (a reference to the window for toasts/dialogs/navigation, the registry, etc.) — pick a clean, consistent way to pass that shared context (constructor injection is probably simplest) rather than each flow class reaching back into a global.
- `page_instance_detail.py` similarly splits its tab-building code by feature area, mirroring the same grouping so the split is consistent across both files rather than using a different taxonomy in each.

**Deliverable for this ticket specifically: just the proposed structure + a short list of which existing methods map to which new module**, reviewed before you start actually moving code — cheap to correct a plan, expensive to correct a half-finished move.

## Ticket R.2 — Move instance lifecycle flows
- Move: `_run_simple_flow`, `start_flow`, `_repair_flow`, `_confirm_cb`, `_handle_collision`, `_set_primary_flow`, `_switch_flow`, `_track_flow`, `_untrack_flow`, `_do_untrack`, `_discover_flow`, `_show_discover_dialog`, `_remove_flow`, `_do_remove`, `_after_remove` (and any I've missed that clearly belong to this group — use your own read of the code, this list is a starting point, not a strict contract).
- Run full test suite. Manual smoke: start/stop/restart/switch-db/remove one real instance.

## Ticket R.3 — Move database operations flows
- Move: `_init_db_flow`, `_backup_flow`, `_drop_db_flow`, `_restore_flow`, `_restore_dialog`, `_validate_flow`, `_show_validate_report`, `_refresh_detail_dbs`, `_load_db_states`, `_apply_db_states`.
- Run full test suite. Manual smoke: List/Init/Drop/Backup/Restore/Validate on a real instance.

## Ticket R.4 — Move module management flows
- Move: `_mod_instance`, `_load_modules`, `_apply_modules`, `_mod_install_flow`, `_refresh_modules_only`, `_mod_update_flow`, `_mod_update_code_flow`, `_backup_before_update`, `_run_update_code`, `_mod_uninstall_flow`, `_mod_deps_dialog`, `_show_deps_dialog`, `_try_dependency_webview`, `_mod_scaffold_wizard`.
- Run full test suite. Manual smoke: List/Install/Update/Uninstall/Diff/Dependency Graph/Scaffold.

## Ticket R.5 — Move configuration flows
- Move: `_conf_save_flow`, `_show_conf_saved`, `_conf_restore_flow`, `_conf_regenerate_flow`, `_meta_save_flow`, `_addons_manage_dialog`, `_addons_edit`, `_addons_move`, `_addons_remove`, `_refresh_conf_tab`.
- Run full test suite. Manual smoke: edit/save/restore/regenerate conf, full Addon Path Manager round-trip.

## Ticket R.6 — Move logs & monitoring flows
- Move: `_log_search_flow`, `_show_search_results`, `_log_doctor_flow`, `_show_doctor_findings`, `_slow_refresh_flow`, `_show_slow`, `_profile_flow`, `_install_pyspy_then_profile`, `_run_profile`, `_show_profile_result`, `_view_svg`, plus the event panel methods (`_on_events_toggled`, `_event_start`, `_event_stop`, `_event_poll_tick`, `_event_flush_tick`, `_on_event_clear_view`, `_on_event_row_setup`, `_on_event_row_bind`) — the event panel is arguably part of the window shell rather than a per-instance flow, use your judgment on whether it stays in `window_main.py` or moves to `logs_monitoring.py`, note which and why.
- Run full test suite. Manual smoke: log tail/search/doctor, event panel live updates during a real action.

## Ticket R.7 — Move Dev Tools flows
- Move: `_rpc_session`, `_rpc_connect_flow`, `_show_rpc_connected`, `_dev_client`, `_dev_list_models`, `_show_models`, `_dev_model_selected`, `_show_metadata`, `_rec_domain`, `_dev_records_page`, `_show_records`, `_rec_search_flow`, `_rec_page_flow`, `_rec_new_flow`, `_rec_edit_flow`, `_show_edit_dialog`, `_coerce_value`, `_record_edit_dialog`, `_rec_delete_flow`, `_cron_refresh_flow`, `_show_crons`, `_launch_json_flow`, `_open_editor_flow`, `_shell_session`, `_shell_start_flow`, `_show_shell_started`, `_shell_timer_ensure`, `_shell_poll_tick`, `_shell_send_flow`, `_shell_stop_flow`, `_devmode_flow`, `_devmode_start`, `_devmode_start_process`, `_devmode_attach`, `_monitor_tree`, `_devmode_tick`, `_devmode_stop`, `_sync_devmode_toggle`, `_test_run_flow`.
- This is the largest single move (Dev Tools accumulated the most methods) — consider whether it needs its own further split (e.g. `dev_tools_rpc.py` for inspector/records/cron vs `dev_tools_process.py` for shell/dev-mode-watch/tests) rather than one more large file replacing the old large file. Your call, note your reasoning.
- Run full test suite. Manual smoke: Model Inspector, Record Browser, Cron Jobs, Shell REPL, Dev Mode Watch, Run Tests.

## Ticket R.8 — Decompose `page_instance_detail.py` to match
- Mirror the same feature grouping for the tab-building/rendering code in this file. Exact approach is your call — this file is about building widgets/tabs rather than handling flows, so the natural split might be "one method/small class per tab" rather than mirroring `flows/` exactly; use whatever grouping makes the file's actual structure clearer, don't force a mechanical 1:1 mapping if it doesn't fit.
- Run full test suite. Manual smoke: open every tab on a real instance, confirm all render correctly.

## Ticket R.9 — Final pass
- Re-read the now-slimmed `window_main.py` — confirm it reads as "window shell + composition," not as a grab-bag with a few things left over. If leftover methods don't cleanly belong anywhere, that's fine to leave in the window shell, just make sure it's a deliberate judgment, not stragglers from running out of steam.
- Full test suite one more time. Full manual pass through the entire v2.0.0 verification checklist's sections 1–6 (not because we expect new bugs, but because "the refactor didn't change behavior" is exactly the kind of claim that needs proving, not asserting).

---

## Report-back template
```
Ticket R.1 (target structure + method mapping): [done] — structure used (if
  different from proposed, note why)
Ticket R.2 (instance lifecycle): [done/blocked] — test suite status
Ticket R.3 (database ops): [done/blocked] — test suite status
Ticket R.4 (module management): [done/blocked] — test suite status
Ticket R.5 (configuration): [done/blocked] — test suite status
Ticket R.6 (logs & monitoring, incl. event panel placement decision): [done/blocked]
Ticket R.7 (dev tools, incl. further-split decision): [done/blocked]
Ticket R.8 (page_instance_detail.py decomposition): [done/blocked]
Ticket R.9 (final pass + full checklist re-verification): [done/blocked]
Final line counts: window_main.py now ___ lines, page_instance_detail.py now ___ lines
Deviations: ...
Open questions for PM: ...
```
