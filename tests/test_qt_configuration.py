"""PSQ-6: Configuration view + flows (offscreen, pytest-qt).

Hermetic registry (tmp DB) + tmp conf file. Manager toggle→apply is
proven live (E2E); here the dialog must open with real checkbox rows.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from odoo_vite.ui_qt.flows.configuration import ConfigurationFlows  # noqa: E402
from odoo_vite.ui_qt.views.configuration import ConfigurationPage  # noqa: E402
from odoo_vite.ui_qt.widgets.selection_list import SelectionList  # noqa: E402


@pytest.fixture()
def _seeded(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-conf.db"))
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_host = localhost\ndb_user = odoo\n"
                    "addons_path = /srv/addons,/srv/extra\n")
    inst = Instance(name="ConfT", version="17.0", path=str(tmp_path),
                    port=8095, primary_db="d",
                    conf_path=str(conf), description="t",
                    workers=2, log_level="debug")
    assert create_instance(inst).ok
    return inst


def test_conf_page_renders(qapp, qtbot, _seeded):
    page = ConfigurationPage()
    qtbot.addWidget(page)
    page.show_instance(_seeded)
    assert page.conf_table.count() >= 3
    assert "localhost" in page.conf_entries["db_host"].text()
    assert "/srv/addons" in page.lbl_addons_ro.text()
    assert page.entry_description.text() == "t"
    assert page.spin_workers.value() == 2
    assert page.drop_log_level.currentText() == "debug"
    assert not page.btn_conf_restore.isEnabled()  # no backup yet


def test_conf_save_collects_diffs(qapp, qtbot, _seeded):
    page = ConfigurationPage()
    qtbot.addWidget(page)
    page.show_instance(_seeded)
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.btn_conf_save.click()
    assert fired == []  # no changes -> notice, no emit
    assert page.lbl_conf_notice.text() == "No changes to save."
    page.conf_entries["db_host"].setText("dbhost2")
    page.btn_conf_save.click()
    assert fired[-1] == ("conf-save", _seeded.id, {"db_host": "dbhost2"})
    # raw editor path (value set + empty-deletes-key)
    fired.clear()
    page.entry_raw_key.setText("workers")
    page.entry_raw_value.setText("4")
    page._on_raw_set()
    assert fired[-1] == ("conf-save", _seeded.id, {"workers": "4"})
    page.entry_raw_key.setText("workers")
    page.entry_raw_value.setText("")
    page._on_raw_set()
    assert fired[-1] == ("conf-save", _seeded.id, {"workers": None})


def test_meta_save_validates_python(qapp, qtbot, _seeded):
    page = ConfigurationPage()
    qtbot.addWidget(page)
    page.show_instance(_seeded)
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.entry_python.setText("/no/such/python")
    page._on_meta_save()
    assert "Not an executable" in page.err_python.text()
    assert fired == []
    page.entry_python.setText("")
    page._on_meta_save()
    assert fired[-1][0] == "meta-save"
    assert fired[-1][2]["log_level"] == "debug"


def test_conf_flows_fail_safe_unknown_instance(qapp, qtbot):
    flows = ConfigurationFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.save("no-such-id", {"a": "b"})
    flows.restore("no-such-id")
    flows.meta_save("no-such-id", {"workers": 0})
    qtbot.wait(4000)
    assert sum("disappeared" in m for m in messages) >= 3


def test_addon_manager_opens_with_checkboxes(qapp, qtbot, _seeded):
    flows = ConfigurationFlows()
    messages = []
    flows.message.connect(messages.append)

    def _close():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                picker = w.findChild(SelectionList)
                if picker is not None:
                    rows = picker.visible_count()
                    checks = picker.checked_ids()
                    print(f"MGR rows={rows} checked={checks}")
                    assert rows == 2
                    assert sorted(checks) == ["/srv/addons", "/srv/extra"]
                w.reject()
                return

    QTimer.singleShot(1500, _close)
    flows.addons_manage(None, _seeded.id)
    qtbot.wait(500)
