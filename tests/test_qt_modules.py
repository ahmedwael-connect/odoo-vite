"""PSQ-5: Modules view + flows (offscreen, pytest-qt).

State-category mapping locked (GTK parity); destructive/DB flows on
failure paths only (live E2E covers the rest).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.flows.modules import ModuleFlows  # noqa: E402
from odoo_vite.ui_qt.views.modules import ModulesPage, state_category  # noqa: E402


def _mod(name, state, installed="", available="", latest="",
         summary=""):
    return {"name": name, "state": state, "installed_version": installed,
            "available_version": available, "latest_version": latest,
            "summary": summary}


def test_state_categories():
    assert state_category(_mod("a", "installed", "17.0", "17.0",
                               "17.0")) == "Installed"
    assert state_category(_mod("b", "installed", "17.0", "17.1",
                               "17.0")) == "Upgradeable"
    assert state_category(_mod("c", "installed", "17.0", "17.0",
                               "17.1")) == "Upgradeable"
    assert state_category(_mod("d", "uninstalled")) == "Installable"
    assert state_category(_mod("e", "to install")) == "Upgradeable"


def _inst_dict():
    return {"id": "m1", "name": "Demo", "status": "stopped",
            "version": "17.0", "primary_db": "d"}


def _mods():
    return [_mod("sale", "installed", "17.0", "17.0", "17.0", "Sales"),
            _mod("crm", "uninstalled", summary="CRM"),
            _mod("stock", "installed", "17.0", "17.1", "17.0", "Stock")]


def test_modules_page_renders_filters_checks(qapp, qtbot):
    page = ModulesPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    page.set_modules(_mods(), {})
    assert page.mod_list.visible_count() == 3
    page.entry_search.setText("crm")
    assert page.mod_list.visible_count() == 1
    page.entry_search.setText("")
    page.state_filter.setCurrentText("Installable")
    assert page.mod_list.visible_count() == 1
    page.state_filter.setCurrentText("All")
    assert page.mod_list.set_checked("crm", True)
    assert page.mod_list.checked_ids() == ["crm"]
    # Filtering must not drop picks.
    page.entry_search.setText("sale")
    page.entry_search.setText("")
    assert page.mod_list.checked_ids() == ["crm"]


def test_modules_page_actions(qapp, qtbot):
    page = ModulesPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    page.set_modules(_mods(), {})
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.mod_list.set_checked("crm", True)
    page.btn_install.click()
    assert fired[-1] == ("mod-install", "m1", ["crm"])
    page.btn_update.click()
    assert fired[-1] == ("mod-update", "m1", ["crm"])
    assert not page.btn_uninstall.isEnabled()
    page.mod_list.view.setCurrentIndex(
        page.mod_list.view.model().index(0, 0))
    assert page.btn_uninstall.isEnabled()
    page.btn_uninstall.click()
    assert fired[-1][0] == "mod-uninstall"
    page.btn_deps.click()
    assert fired[-1][0] == "mod-deps"


def test_module_flows_fail_safe_unknown_instance(qapp, qtbot):
    flows = ModuleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.refresh_modules("no-such-id")
    flows.install("no-such-id", ["x"])
    flows.update("no-such-id", ["x"])
    flows.uninstall("no-such-id", "x")
    flows.update_code("no-such-id")
    flows.show_deps("no-such-id", "x")
    qtbot.wait(4000)
    assert any("Instance disappeared" in m or "No primary" in m
               or "not" in m.lower() for m in messages)
    assert len(messages) >= 4
