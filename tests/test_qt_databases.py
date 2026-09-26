"""PSQ-4: Databases view + flows (offscreen, pytest-qt).

A.1 rule under test: only the Likely group arrives pre-checked.
Destructive/DB flows on failure paths only (live E2E covers the rest).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.flows.databases import (  # noqa: E402
    DatabaseFlows,
    group_discover_entries,
)
from odoo_vite.ui_qt.views.databases import DatabasesPage  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Qt tests never touch the real registry (keyring/dbus abort risk)."""
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-db.db"))


def _entries():
    return [
        {"name": "db_new", "initialized": True, "odoo_major": "17.0"},
        {"name": "db_old", "initialized": True, "odoo_major": "16.0"},
        {"name": "db_raw", "initialized": False, "odoo_major": ""},
        {"name": "db_new2", "initialized": True, "odoo_major": "17.0"},
    ]


def test_grouping_likely_other_plain():
    groups = group_discover_entries(_entries(), "17.0")
    assert groups["likely"] == ["db_new", "db_new2"]
    assert groups["other"] == ["db_old"]
    assert groups["plain"] == ["db_raw"]


def test_grouping_empty_and_missing_version():
    assert group_discover_entries([], "17.0") == {
        "likely": [], "other": [], "plain": []}
    groups = group_discover_entries(_entries(), "")
    assert groups["likely"] == []  # no version match possible
    assert sorted(groups["other"]) == ["db_new", "db_new2", "db_old"]


def _inst_dict():
    return {"id": "db1", "name": "Demo", "status": "stopped",
            "version": "17.0", "tracked_dbs": ["main", "extra"],
            "primary_db": "main"}


def test_databases_page_renders_and_gates(qapp, qtbot):
    page = DatabasesPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    assert page.tracked_list.topLevelItemCount() == 2
    assert page.db_combo.count() == 2
    assert page.db_combo.currentText() == "main"
    # Primary selected by default (combo) -> Drop disabled (B.4 parity).
    page.tracked_list.setCurrentItem(page.tracked_list.topLevelItem(0))
    assert not page.btn_drop.isEnabled()
    page.tracked_list.setCurrentItem(page.tracked_list.topLevelItem(1))
    assert page.btn_drop.isEnabled()
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.btn_backup.click()
    assert fired == [("backup-db", "db1", "extra")]
    page.btn_discover.click()
    assert fired[-1] == ("discover", "db1", None)


def test_databases_page_init_gated_by_state(qapp, qtbot):
    page = DatabasesPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    page.tracked_list.setCurrentItem(page.tracked_list.topLevelItem(0))
    page.set_db_states({"main": {"exists": True, "initialized": True},
                        "extra": {"exists": True, "initialized": False}})
    assert "initialized" in page.tracked_list.topLevelItem(0).text(3)
    page.tracked_list.setCurrentItem(page.tracked_list.topLevelItem(0))
    assert not page.btn_init.isEnabled()
    page.tracked_list.setCurrentItem(page.tracked_list.topLevelItem(1))
    assert page.btn_init.isEnabled()


def test_db_states_render_text(qapp, qtbot):
    page = DatabasesPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    page.set_db_states({"main": {"exists": True, "initialized": True,
                                 "odoo_version": "17.0", "size": "1 MB"},
                        "extra": {"exists": False, "initialized": False}})
    assert "v17.0" in page.tracked_list.topLevelItem(0).text(2)
    assert "missing" in page.tracked_list.topLevelItem(1).text(3)


def test_databases_empty_state(qapp, qtbot):
    from odoo_vite.ui_qt.views.databases import DatabasesPage as _P

    page = _P()
    qtbot.addWidget(page)
    page.show()
    page.show_instance({"id": "e", "tracked_dbs": [], "primary_db": ""})
    assert page.lbl_tracked_empty.isVisible()
    page.show_instance({"id": "e", "tracked_dbs": ["d1"],
                        "primary_db": "d1"})
    assert not page.lbl_tracked_empty.isVisible()


def test_db_flows_fail_safe_unknown_instance(qapp, qtbot):
    flows = DatabaseFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.init_db("no-such-id", "dbx")
    flows.drop_db("no-such-id", "dbx")
    flows.backup_db("no-such-id", "dbx", "/tmp/nowhere.dump")
    flows.restore_db("no-such-id", "/tmp/nowhere.dump", "t")
    flows.validate("no-such-id")
    qtbot.wait(4000)
    assert len(messages) >= 5
    assert sum("no-such-id" in m or "Instance not found" in m
               for m in messages) >= 5


@pytest.fixture(autouse=True)
def _drain_workers():
    """No QThread may outlive its test (teardown abort otherwise)."""
    yield
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background(), "background workers did not finish"


def test_track_many_batches_sequentially(qapp, qtbot, tmp_path, monkeypatch):
    """Lost-update regression: 3 parallel tracks kept only 1 (live E2E).
    track_many must land all of them."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance, get_instance
    from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "batch.db"))
    inst = Instance(name="Batch", version="17.0", path=str(tmp_path),
                    port=8099, primary_db="main", tracked_dbs=["main"])
    assert create_instance(inst).ok
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.track_many(inst.id, ["a1", "a2", "a3"])
    qtbot.wait(4000)
    tracked = get_instance(inst.id).tracked_dbs
    assert sorted(tracked) == ["a1", "a2", "a3", "main"], tracked
    assert any("3 database" in m for m in messages)
