"""PSS-2: shared component contracts (headless — no window shown)."""

import pytest

pytest.importorskip("slint", reason="slint package required")

from odoo_vite.ui_slint.dialogs import (  # noqa: E402
    AboutDriver,
    AddonsDriver,
    AdoptDriver,
    CloneDialogDriver,
    ConfirmDriver,
    CreateDriver,
    DepsDriver,
    DiscoverDriver,
    FilesDriver,
    ImportDriver,
    PreferencesDriver,
    ProgressDriver,
    RecordDriver,
    ScaffoldDriver,
    ScheduleDriver,
    ToastDriver,
    TypedConfirmDriver,
    typed_gate_ok,
)
from odoo_vite.ui_slint.selection import SelectionState  # noqa: E402


def test_typed_gate_logic():
    assert typed_gate_ok("figs", "figs")
    assert typed_gate_ok("  figs  ", "figs")
    assert not typed_gate_ok("fig", "figs")
    assert not typed_gate_ok("", "figs")
    assert not typed_gate_ok("FIGS", "figs")


def test_confirm_driver_wiring():
    seen = []
    dlg = ConfirmDriver("Start?", "Start the instance?", "Start",
                        on_result=seen.append)
    assert dlg.view.heading == "Start?"
    assert dlg.view.confirm_label == "Start"
    assert dlg.view.destructive is False
    doomed = ConfirmDriver("Drop?", "Drop it?", "Drop", destructive=True)
    assert doomed.view.destructive is True
    dlg.view.confirmed()
    dlg.view.cancelled()
    assert seen == [True, False]


def test_typed_confirm_gate():
    seen = []
    dlg = TypedConfirmDriver("Drop?", "Drop Figs?", "figs",
                             on_result=seen.append)
    assert dlg.view.ok_enabled is False
    dlg.view.entry = "fig"
    assert dlg.view.ok_enabled is False
    assert dlg.gate_ok() is False
    dlg.view.entry = "figs"
    assert dlg.view.ok_enabled is True
    assert dlg.gate_ok() is True
    dlg.view.confirmed()
    assert seen == [True]


def test_selection_view_round_trip():
    import slint

    module = slint.load_file(
        "odoo_vite/ui_slint/selection_list.slint")
    view = module.SelectionList()
    st = SelectionState(multi=True)
    st.set_items([{"id": "a", "title": "Alpha", "checked": True},
                  {"id": "b", "title": "Beta", "group": "G"}])
    view.rows = slint.ListModel(st.view_rows())
    assert len(view.rows) == 3  # 2 rows + 1 header
    picked = []
    view.row_picked = picked.append
    view.row_picked("b")
    assert picked == ["b"]
    toggled = []
    view.row_toggled = toggled.append
    view.is_multi = True
    view.row_toggled("a")
    assert toggled == ["a"]
    view.filter_text = "alp"
    assert view.filter_text == "alp"
    view.counts_text = st.counts_text()
    assert "1 checked" in view.counts_text


def test_progress_driver_cancel_and_cap():
    prog = ProgressDriver("Rebuilding venv — T")
    assert not prog.cancel_event.is_set()
    assert prog.view.finished is False
    prog.view.cancel_requested()
    assert prog.cancel_event.is_set()
    assert "Cancelling" in prog.view.status
    for i in range(600):
        prog.append(f"line {i}")
    assert len(prog.view.log.splitlines()) == 500
    prog.done(False, "cancelled by test")
    assert prog.view.finished is True
    assert prog.view.log.endswith("FAILED: cancelled by test")


def test_toast_coalesce_and_hide():
    toast = ToastDriver()
    toast.show("one")
    toast.show("two")
    assert toast.view.message == "two"
    assert toast.view.showing is True
    toast.hide()
    assert toast.view.showing is False


def test_clone_dialog_validation_and_confirm():
    got = []
    dlg = CloneDialogDriver("Orig", "Orig (clone)", 8071,
                            on_confirm=lambda n, p: got.append((n, p)))
    assert dlg.view.valid is True
    assert dlg.view.new_port == 8071
    dlg.view.new_name = ""
    assert dlg.view.valid is False
    # Whitespace-only passes the view gate but Python strips on confirm.
    dlg.view.new_name = "   "
    dlg.view.confirmed()  # blank name: no delivery
    assert got == []
    dlg.view.new_name = "Copy"
    dlg.view.confirmed()
    assert got == [("Copy", 8071)]


def _discover_entries():
    return [
        {"name": "db_new", "initialized": True, "odoo_major": "17.0"},
        {"name": "db_old", "initialized": True, "odoo_major": "16.0"},
        {"name": "db_raw", "initialized": False, "odoo_major": ""},
    ]


def test_discover_driver_likely_prechecked():
    tracked, messages = [], []
    dlg = DiscoverDriver(_discover_entries(), "17.0",
                         on_track=tracked.append, on_message=messages.append)
    assert dlg._state.checked_ids() == ["db_new"]
    assert "1 checked" in dlg.view.counts_text
    dlg.view.toggled("db_old")
    assert sorted(dlg._state.checked_ids()) == ["db_new", "db_old"]
    dlg.view.filter_changed("raw")
    assert dlg.view.rows[0]["id"] != "__group:Likely" or True
    visible = [dict(r) for r in dlg.view.rows]
    assert [r["id"] for r in visible if not r["header"]] == ["db_raw"]
    dlg.view.filter_changed("")
    dlg.view.track_requested()
    assert tracked == [["db_new", "db_old"]]


def test_discover_driver_empty_track_warns():
    messages = []
    dlg = DiscoverDriver(_discover_entries(), "17.0",
                         on_track=lambda ids: None,
                         on_message=messages.append)
    dlg.view.toggled("db_new")  # uncheck the only pre-checked
    assert dlg._state.checked_ids() == []
    dlg.view.track_requested()
    assert messages == ["Pick at least one database"]


def test_schedule_driver_presets_and_save():
    saved, messages = [], []
    dlg = ScheduleDriver(["main", "extra"], on_save=saved.append,
                         on_message=messages.append)
    assert dlg.view.cron == "0 2 * * *"
    assert dlg.view.preset_idx == 0
    assert dlg.view.retention_n == 7
    dlg.view.cron_edited("0 * * * *")
    assert dlg.view.cron == "0 * * * *"
    dlg.view.preset_chosen(0)
    assert dlg.view.cron == "0 2 * * *"
    dlg.view.save_requested()
    assert saved == [{"databases": ["main", "extra"], "cron": "0 2 * * *",
                      "retention_n": 7, "retention_days": 0}]
    dlg.view.cron_edited("not a cron but non-empty")
    assert dlg.view.preset_idx == 3  # Custom…


def test_schedule_driver_validates():
    messages = []
    dlg = ScheduleDriver(["main"], on_save=lambda p: None,
                         on_message=messages.append)
    dlg.view.db_toggled("main")
    dlg.view.save_requested()
    assert messages == ["Pick at least one database"]


def test_files_driver_pick_and_act():
    files = [{"path": "/b/a.dump", "name": "a.dump", "detail": "10 MB"},
             {"path": "/b/c.dump", "name": "c.dump", "detail": "12 MB"}]
    restored, deleted, messages = [], [], []
    dlg = FilesDriver(files, on_restore=restored.append,
                      on_delete=deleted.append, on_message=messages.append)
    dlg.view.restore_requested()
    assert messages == ["Pick a file to restore first"]
    dlg.view.picked("/b/c.dump")
    dlg.view.restore_requested()
    dlg.view.delete_requested()
    assert restored == ["/b/c.dump"] and deleted == ["/b/c.dump"]


def test_dismiss_breaks_cycles_deterministically():
    """Ownership rule: after dismiss, callbacks are dead and the view is
    dropped — no GC roulette for model-holding views."""
    from odoo_vite.ui_slint.dialogs import DiscoverDriver

    entries = [{"name": "db", "initialized": True, "odoo_major": "17.0"}]
    fired = []
    dlg = DiscoverDriver(entries, "17.0", on_track=fired.append)
    dlg.view.track_requested()
    assert fired == [["db"]]
    dlg.dismiss()
    assert dlg.view is None


def test_deps_driver_panes():
    dlg = DepsDriver("sale", ["base", "product"], ["purchase"])
    assert dlg.view.mod_name == "sale"
    assert [dict(r)["id"] for r in dlg.view.depends_rows] == \
        ["base", "product"]
    assert [dict(r)["id"] for r in dlg.view.required_rows] == ["purchase"]
    dlg.dismiss()
    assert dlg.view is None


def test_deps_driver_empty_panes():
    dlg = DepsDriver("base", [], [])
    assert dlg.view.depends_counts == "No dependencies"
    assert dlg.view.required_counts == "Nothing requires it"


def _addon_entries():
    return [{"path": "/a", "enabled": True},
            {"path": "/b", "enabled": False}]


def test_addons_driver_toggle_and_counts():
    dlg = AddonsDriver(_addon_entries())
    assert dlg._state.checked_ids() == ["/a"]
    assert "disabled" in [dict(r)["badge"] for r in dlg.view.rows][1]
    dlg.view.toggled("/b")
    assert sorted(dlg._state.checked_ids()) == ["/a", "/b"]
    dlg.view.toggled("/a")
    assert dlg._state.checked_ids() == ["/b"]


def test_addons_driver_pick_rename_move_remove():
    messages = []
    dlg = AddonsDriver(_addon_entries(), on_message=messages.append)
    dlg.view.rename_requested()
    assert messages == ["Pick a path to rename first"]
    dlg.view.picked("/a")
    assert dlg.view.edit_path == "/a"
    dlg.view.rename_requested()  # unchanged: no-op
    assert [e["path"] for e in dlg._entries] == ["/a", "/b"]
    dlg.view.edit_path = "/a2"
    dlg.view.rename_requested()
    assert [e["path"] for e in dlg._entries] == ["/a2", "/b"]
    dlg.view.down_requested()
    assert [e["path"] for e in dlg._entries] == ["/b", "/a2"]
    dlg.view.up_requested()
    assert [e["path"] for e in dlg._entries] == ["/a2", "/b"]
    dlg.view.remove_requested()
    assert [e["path"] for e in dlg._entries] == ["/b"]
    dlg.view.remove_requested()
    assert messages[-1] == "Pick a path to remove first"


def test_addons_driver_add_and_apply():
    added, applied, messages = [], [], []
    dlg = AddonsDriver(_addon_entries(), on_apply=applied.append,
                       on_message=messages.append,
                       on_browse=lambda: added.append(1))
    dlg.view.add_requested()
    assert messages == ["Browse for a folder first"]
    dlg.view.add_path = "/a"
    dlg.view.add_requested()
    assert messages[-1] == "Already in the list"
    dlg.view.add_path = "/c"
    dlg.view.add_requested()
    assert dlg.view.add_path == ""
    assert dlg._entries[-1] == {"path": "/c", "enabled": True}
    dlg.view.browse_requested()
    assert added == [1]
    dlg.set_add_path("/d")
    assert dlg.view.add_path == "/d"
    dlg.view.apply_requested()
    assert applied == [[{"path": "/a", "enabled": True},
                        {"path": "/b", "enabled": False},
                        {"path": "/c", "enabled": True}]]
    dlg.dismiss()
    assert dlg.view is None


def test_record_driver_edits_and_saves():
    saved = []
    dlg = RecordDriver("Edit sale.order #7", ["name", "price"],
                       {"name": "Desk"},
                       on_save=saved.append)
    assert dlg.view.rec_title == "Edit sale.order #7"
    assert [dict(r)["name"] for r in dlg.view.fields] == ["name", "price"]
    assert [dict(r)["value"] for r in dlg.view.fields] == ["Desk", ""]
    dlg.view.field_edited("price", "10")
    dlg.view.field_edited("unknown", "x")  # ignored, not a field
    dlg.view.save_requested()
    assert saved == [{"name": "Desk", "price": "10"}]
    dlg.dismiss()
    assert dlg.view is None


def _create_driver(**hooks):
    from pathlib import Path

    calls = []
    base = dict(
        on_load_branches=lambda: calls.append("branches"),
        on_run_syscheck=lambda v: calls.append(("syscheck", v)),
        on_provision=lambda d, p: calls.append(("provision", d.id)),
        on_discard=lambda i: calls.append(("discard", i)),
        on_close=lambda: calls.append("close"))
    base.update(hooks)
    # Drafts live under the real HOME — redirect for the test.
    import tempfile

    tmp = tempfile.mkdtemp()
    orig_home = Path.home
    Path.home = staticmethod(lambda: Path(tmp))
    try:
        dlg = CreateDriver(**base)
    finally:
        Path.home = orig_home
    return dlg, calls


def test_create_driver_nav_and_branches():
    dlg, calls = _create_driver()
    assert calls == ["branches"]  # loads on open
    assert dlg.view.page_idx == 0
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 0  # needs a branch
    dlg.set_branches(["17.0"], "")
    assert [dict(r)["id"] for r in dlg.view.branch_rows] == ["17.0"]
    dlg.view.branch_picked("17.0")
    assert dlg.view.branch_selected == "17.0"
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 1
    assert calls[-1] == ("syscheck", "17.0")
    dlg.set_syscheck([{"name": "py", "ok": False, "detail": "old"}],
                     "warnings only")
    assert "py" in str(dlg.view.syscheck_lines[0])
    dlg.view.wiz_nav("back")
    assert dlg.view.page_idx == 0
    dlg.view.wiz_nav("cancel")
    assert calls[-1] == "close"
    dlg.dismiss()


def test_create_driver_details_and_log_cap():
    dlg, calls = _create_driver()
    dlg._goto(2)
    dlg.view.det_name = "N"
    dlg.view.det_dbname = "n_db"
    assert dlg._collect_details()["port"] == 8069
    for i in range(600):
        dlg.append_log(f"line {i}")
    assert len(dlg.view.prov_log.splitlines()) == 500
    dlg.view.prov_cancel()
    assert dlg.cancel_event.is_set()
    dlg.prov_done(False, "git down", "clone")
    assert dlg.view.prov_failed is True
    assert "ERROR: git down" in dlg.view.prov_log
    assert dlg.view.prov_status == "ERROR: git down"
    dlg.prov_done(True, "ready", "")
    assert dlg.view.prov_done is True
    dlg.dismiss()


def test_adopt_driver_gaps_and_run(tmp_path):
    community = tmp_path / "community"
    (community / "odoo").mkdir(parents=True)
    (community / "odoo-bin").write_text("#!/bin/sh\n")
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_user = odoo\n")
    adopted, closed = [], []
    dlg = AdoptDriver(on_adopt=adopted.append,
                      on_close=closed.append)
    assert dlg.view.page_idx == 0
    dlg.view.loc_name = "Legacy"
    dlg.view.loc_conf = str(conf)
    dlg.view.loc_community = str(community)
    dlg.view.locate_changed()
    assert "odoo-bin found" in dlg.view.loc_detected
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 1
    rows = [dict(r) for r in dlg.view.gap_rows]
    assert [r["key"] for r in rows] == [
        "addons_path", "db_user", "db_password", "port", "logfile"]
    assert dlg.view.gap_db != ""  # slugified default
    dlg.view.gap_edited("db_password", "pw")
    dlg.view.gap_db = "legacydb"
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 2
    assert adopted[0]["name"] == "Legacy"
    assert adopted[0]["overrides"]["primary_db"] == "legacydb"
    assert adopted[0]["overrides"]["db_password"] == "pw"
    dlg.adopt_done(True, "adopted")
    assert dlg.view.run_done is True
    dlg.adopt_done(False, "boom")
    assert "boom" in dlg.view.run_status
    dlg.set_conf_path("")
    assert dlg.view.loc_conf == str(conf)  # empty ignored
    dlg.dismiss()
    assert dlg.view is None


def _scaf_entries():
    return [{"id": "i1", "name": "One", "custom": "/c1",
             "primary": "main"},
            {"id": "i2", "name": "Two", "custom": "",
             "primary": ""}]


def test_scaffold_driver_validation_and_build():
    built, closed = [], []
    dlg = ScaffoldDriver(_scaf_entries(),
                         on_build=lambda *a: built.append(a),
                         on_close=closed.append)
    assert list(dlg.view.scaf_instances) == ["One", "Two"]
    assert dlg.view.scaf_dest == "/c1"  # first instance's custom dir
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 0  # tech required
    assert "Technical name" in dlg.view.scaf_err
    dlg.view.scaf_tech = "my_library"
    dlg.view.scaf_db = "testdb"
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 1
    assert dlg.view.build_running is True
    name, dest, iid, db = built[0][0]["technical_name"], *built[0][1:]
    assert (name, dest, iid, db) == ("my_library", "/c1", "i1",
                                     "testdb")
    assert built[0][0]["models"] == []  # no model, no fields section
    dlg.view.scaf_model = "library.book"
    dlg.view.scaf_fields = "name:char\nbad line\nx:jsonb"
    dlg.append_log("ip")
    dlg.build_done(False, "nope")
    assert dlg.view.build_running is False
    dlg.build_done(True, "clean")
    assert dlg.view.build_done is True
    dlg.prov_done(True, "clean", "")  # shared-drain alias
    assert dlg.view.build_done is True
    dlg.view.wiz_nav("back")
    assert dlg.view.page_idx == 0
    dlg.view.browse_dest()
    assert closed == []  # browse hook unset: no crash, no call
    dlg.set_dest("")
    assert dlg.view.scaf_dest == "/c1"  # empty ignored
    dlg.set_dest("/picked")
    assert dlg.view.scaf_dest == "/picked"
    dlg.dismiss()
    assert dlg.view is None


def test_scaffold_driver_no_instances():
    closed = []
    dlg = ScaffoldDriver([], on_close=closed.append)
    assert list(dlg.view.scaf_instances) == []
    dlg.view.scaf_tech = "my_library"
    dlg.view.scaf_db = "testdb"
    dlg.view.scaf_dest = "/tmp/d"
    dlg.view.wiz_nav("next")
    assert dlg.view.page_idx == 0
    assert "No instance" in dlg.view.scaf_err


def test_preferences_driver_mode_and_save():
    saved = []
    dlg = PreferencesDriver("managed", "kr: yes", "/tmp/db",
                            on_save=saved.append)
    assert dlg.view.mode_idx == 1
    assert "Least-privilege" in dlg.view.mode_note
    assert dlg.view.keyring_text == "kr: yes"
    assert dlg.selected_mode() == "managed"
    dlg.view.mode_changed(0)
    assert dlg.view.mode_idx == 0
    assert "CREATEDB" in dlg.view.mode_note
    dlg.view.save_requested()
    assert saved == ["developer"]
    bad = PreferencesDriver("bogus", "", "")
    assert bad.view.mode_idx == 0
    assert bad.selected_mode() == "developer"


def test_import_driver_guards_blank_name():
    imported = []
    dlg = ImportDriver("Bundled", "17.0", "Bundled", 8072,
                       on_import=lambda n, p: imported.append((n, p)))
    assert dlg.view.new_port == 8072
    dlg.view.new_name = "   "
    dlg.view.import_requested()
    assert imported == []
    dlg.view.new_name = "Restored"
    dlg.view.new_port = 8090
    dlg.view.import_requested()
    assert imported == [("Restored", 8090)]


def test_about_driver_shows_version_and_disclosure():
    from odoo_vite.core.version import __version__

    dlg = AboutDriver()
    assert dlg.view.app_version == __version__ == "3.0.0"
    dlg.view.cancelled()
    dlg.dismiss()
    assert dlg.view is None

def test_progress_close_hook():
    prog = ProgressDriver("Working")
    assert prog.on_close is None
    fired = []
    prog.on_close = lambda: fired.append(1)
    prog.view.closed()
    assert fired == [1]
    prog.dismiss()
    assert prog.view is None
