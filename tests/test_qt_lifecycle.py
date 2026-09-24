"""PSQ-3: sidebar + overview + lifecycle flows (offscreen, pytest-qt).

Destructive/DB-touching flows are exercised on failure paths only
(bogus ids) — success paths need live Postgres/Odoo and are covered by
manual E2E, same rule as the GTK suite.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows  # noqa: E402
from odoo_vite.ui_qt.views.overview import OverviewPage  # noqa: E402
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar  # noqa: E402
from odoo_vite.ui_qt.workers import run_in_background  # noqa: E402


def _inst_dict(iid="x1", name="Demo", status="stopped"):
    return {"id": iid, "name": name, "status": status, "version": "17.0",
            "port": 8069, "path": "/tmp/x", "primary_db": "d",
            "db_user": "odoo"}


def test_worker_runs_off_gui_thread(qapp, qtbot):
    """moveToThread refuses parented workers — run_in_background must not
    parent the worker (verified live warning otherwise)."""
    from PySide6.QtCore import QObject, QThread

    from odoo_vite.ui_qt.workers import CoreWorker

    host = QObject()
    seen = {}
    main_thread = QThread.currentThread()

    def _done(ok, message, data):
        seen["slot_thread_is_main"] = (QThread.currentThread() is main_thread)

    def _core_fn():
        seen["worker_off_main"] = (QThread.currentThread() is not main_thread)
        return Result(ok=True, message="x", data={})

    worker = CoreWorker(_core_fn)
    assert worker.parent() is None
    run_in_background(host, _core_fn, _done)
    qtbot.wait(2000)
    assert seen.get("worker_off_main") is True
    assert seen.get("slot_thread_is_main") is True


def test_worker_delivers_result_to_gui(qapp, qtbot):
    from PySide6.QtCore import QObject

    host = QObject()
    seen = {}

    def _done(ok, message, data):
        seen.update(ok=ok, message=message, data=data)

    def _core_fn(a, b=0):
        return Result(ok=True, message=f"{a}-{b}", data={"k": 1})

    run_in_background(host, _core_fn, _done, "v", b=2)
    qtbot.wait(2000)
    assert seen.get("ok") is True
    assert seen.get("message") == "v-2"
    assert seen.get("data") == {"k": 1}


def test_worker_reports_core_exception(qapp, qtbot):
    from PySide6.QtCore import QObject

    host = QObject()
    seen = {}

    def _done(ok, message, data):
        seen.update(ok=ok, message=message)

    def _boom():
        raise RuntimeError("kablam")

    run_in_background(host, _boom, _done)
    qtbot.wait(2000)
    assert seen.get("ok") is False
    assert "kablam" in seen.get("message", "")


def test_sidebar_lists_and_selects(qapp, qtbot):
    bar = InstanceSidebar()
    qtbot.addWidget(bar)
    bar.set_instances([_inst_dict("a", "One", "running"),
                       _inst_dict("b", "Two", "stopped")])
    assert bar._list.visible_count() == 2
    picked = []
    bar.instanceSelected.connect(picked.append)
    assert bar._list.select_id("b")
    assert picked == ["b"]
    # Refresh preserves selection across status updates.
    bar.set_instances([_inst_dict("a", "One", "stopped"),
                       _inst_dict("b", "Two", "running")])
    assert bar.selected_id() == "b"


def test_overview_shows_and_gates_buttons(qapp, qtbot):
    page = OverviewPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict(status="stopped"))
    assert "Demo" in page.name_label.text()
    assert page._buttons["start"].isEnabled()
    assert not page._buttons["stop"].isEnabled()
    page.refresh_status(_inst_dict(status="running"))
    assert not page._buttons["start"].isEnabled()
    assert page._buttons["stop"].isEnabled()
    assert page._buttons["restart"].isEnabled()
    fired = []
    page.actionRequested.connect(lambda a, i: fired.append((a, i)))
    page._buttons["stop"].click()
    assert fired == [("stop", "x1")]


def test_flows_stop_unknown_instance_fails(qapp, qtbot):
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.stop("no-such-id")
    qtbot.wait(3000)
    assert messages[0] == "Stopping…"
    assert "no-such-id" in messages[-1]


def test_flows_remove_unknown_instance_fails(qapp, qtbot):
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.remove("no-such-id")
    qtbot.wait(3000)
    assert len(messages) >= 2  # "Removing…" + result


def test_flows_discover_unknown_instance(qapp, qtbot):
    flows = LifecycleFlows()
    payloads = []
    flows.discover_entries("no-such-id", payloads.append)
    qtbot.wait(3000)
    assert payloads and payloads[0]["ok"] is False
