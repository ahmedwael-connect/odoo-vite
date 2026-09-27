"""BKQ: backup schedule UI (offscreen, pytest-qt). Hermetic registry.

Timer installation and real dumps are live-E2E only (they touch
systemd/Postgres); here: rendering, validation, toggle/delete, and the
browse dialog chrome.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.flows.backup_schedules import (  # noqa: E402
    BackupSchedulesFlows,
    PRESETS,
)
from odoo_vite.ui_qt.views.databases import DatabasesPage  # noqa: E402


@pytest.fixture()
def _seeded(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-bkq.db"))
    inst = Instance(name="Bkq", version="17.0", path=str(tmp_path),
                    port=8097, primary_db="d", tracked_dbs=["d"])
    assert create_instance(inst).ok
    return inst


def _sched_dict(sid="s1", on=True):
    return {"id": sid, "databases": ["d"], "cron": "0 2 * * *",
            "retention_n": 7, "retention_days": 0, "enabled": on,
            "last_run": "2026-01-01T02:00", "last_status": "ok"}


def test_presets_match_gtk():
    assert ("Daily 02:00", "0 2 * * *") in PRESETS
    assert ("Hourly", "0 * * * *") in PRESETS
    assert ("Weekly Sun 02:00", "0 2 * * 0") in PRESETS


def test_schedules_render_and_gate(qapp, qtbot):
    page = DatabasesPage()
    qtbot.addWidget(page)
    page.show()
    page.show_instance({"id": "x", "tracked_dbs": [], "primary_db": ""})
    page.refresh_schedules([], {"installed": False})
    assert "not installed" in page.lbl_sched_status.text()
    assert page.sched_list.count() == 1
    assert "No schedules" in page.sched_list.item(0).text()
    page.refresh_schedules([_sched_dict()], {"active": True})
    assert "active" in page.lbl_sched_status.text()
    assert page.sched_list.count() == 1
    assert "0 2 * * *" in page.sched_list.item(0).text()
    page.sched_list.setCurrentRow(0)
    assert page.btn_sched_run.isEnabled()
    assert page.btn_sched_delete.isEnabled()
    page.refresh_schedules([_sched_dict(on=False)], {"active": True})
    assert "disabled" in page.sched_list.item(0).text()


def test_sched_actions_emit(qapp, qtbot):
    page = DatabasesPage()
    qtbot.addWidget(page)
    page.show_instance({"id": "x", "tracked_dbs": [], "primary_db": ""})
    page.refresh_schedules([_sched_dict()], {"active": True})
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.sched_list.setCurrentRow(0)
    page.btn_sched_add.click()
    assert fired[-1] == ("sched-add", "x", None)
    page.btn_sched_edit.click()
    assert fired[-1] == ("sched-edit", "x", "s1")
    page.btn_sched_run.click()
    assert fired[-1] == ("sched-run-now", "x", "s1")
    page.btn_sched_toggle.click()
    assert fired[-1] == ("sched-toggle", "x", "s1")
    page.btn_sched_delete.click()
    assert fired[-1] == ("sched-delete", "x", "s1")
    page.btn_backups_browse.click()
    assert fired[-1] == ("backups-browse", "x", None)


def test_create_validates_cron(qapp, qtbot, _seeded):
    flows = BackupSchedulesFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.create_schedule(_seeded.id, ["d"], "nonsense", 7, 0)
    qtbot.wait(2000)
    assert any("Invalid" in m or "cron" in m.lower() for m in messages)


def test_toggle_and_delete_roundtrip(qapp, qtbot, _seeded):
    from odoo_vite.core import backup_scheduler as _bs

    res = _bs.create_schedule(_seeded.id, ["d"], "0 2 * * *")
    assert res.ok
    sid = res.data["id"]
    flows = BackupSchedulesFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.sched_toggle(_seeded.id, sid)
    qtbot.wait(2000)
    assert _bs.get_schedule(sid).enabled is False
    flows.sched_toggle(_seeded.id, sid)
    qtbot.wait(2000)
    assert _bs.get_schedule(sid).enabled is True
    assert _bs.delete_schedule(sid).ok


def test_browse_dialog_opens_empty(qapp, qtbot, _seeded):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    flows = BackupSchedulesFlows()

    def _close():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                w.reject()
                return

    QTimer.singleShot(1200, _close)
    flows.backups_browse_dialog(None, _seeded.id)
    qtbot.wait(500)


def test_flow_update_preserves_history(qapp, qtbot, _seeded):
    from odoo_vite.core import backup_scheduler as _bs
    from odoo_vite.ui_qt.flows.backup_schedules import BackupSchedulesFlows

    res = _bs.create_schedule(_seeded.id, ["d"], "0 2 * * *")
    sid = res.data["id"]
    _bs._record_run(sid, True, "Backed up 1 database(s)")
    before = _bs.get_schedule(sid)
    flows = BackupSchedulesFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.update_schedule(_seeded.id, sid, ["d"], "30 3 * * *", 14, 0)
    qtbot.wait(2000)
    after = _bs.get_schedule(sid)
    assert after.id == sid
    assert after.cron == "30 3 * * *"
    assert after.last_run == before.last_run
    assert any("updated" in m.lower() for m in messages)


def test_edit_dialog_prefills(qapp, qtbot, _seeded):
    """Edit must open prefilled from the schedule (not blank defaults)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog

    from odoo_vite.core import backup_scheduler as _bs
    from odoo_vite.ui_qt.flows.backup_schedules import BackupSchedulesFlows

    res = _bs.create_schedule(_seeded.id, ["d"], "30 3 * * *",
                              retention_n=14, retention_days=30)
    sid = res.data["id"]
    flows = BackupSchedulesFlows()
    seen = {}

    def _grab():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible() \
                    and w.windowTitle().startswith("Edit schedule"):
                from PySide6.QtWidgets import QLineEdit, QSpinBox
                edits = [e for e in w.findChildren(QLineEdit)
                         if "cron expression" in (e.toolTip() or "")]
                assert edits, "cron entry not found"
                seen["cron"] = edits[0].text()
                spins = w.findChildren(QSpinBox)
                seen["spins"] = sorted(sp.value() for sp in spins)
                seen["title"] = w.windowTitle()
                w.reject()
                return

    QTimer.singleShot(1200, _grab)
    flows.sched_edit_dialog(None, _seeded.id, sid)
    qtbot.wait(500)
    assert seen.get("title", "").startswith("Edit schedule")
    assert seen.get("cron") == "30 3 * * *"
    assert seen.get("spins") == [14, 30]
    assert _bs.delete_schedule(sid).ok
