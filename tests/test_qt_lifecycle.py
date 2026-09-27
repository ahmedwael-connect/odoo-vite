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


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Qt tests never touch the real registry (keyring/dbus abort risk)."""
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-lc.db"))


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
    page.btn_browser.click()
    assert fired[-1] == ("browser", "x1")
    page.btn_secure.click()
    assert fired[-1] == ("secure", "x1")


def test_overview_clone_button_gates_and_fires(qapp, qtbot):
    """U5.1: Clone visible, disabled while running/busy, emits clone."""
    page = OverviewPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict(status="stopped"))
    assert page.btn_clone.isEnabled()
    page.refresh_status(_inst_dict(status="running"))
    assert not page.btn_clone.isEnabled()
    page.refresh_status(_inst_dict(status="stopped"))
    fired = []
    page.actionRequested.connect(lambda a, i: fired.append((a, i)))
    page.btn_clone.click()
    assert fired == [("clone", "x1")]
    # Busy gating wins (poll ticks must not re-enable mid-operation).
    page.set_actions_enabled(False)
    assert not page.btn_clone.isEnabled()
    page.set_actions_enabled(True)


def test_overview_measure_disk_fires(qapp, qtbot):
    """U5.3: Measure button emits measure-disk; label resets per instance."""
    page = OverviewPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict(status="stopped"))
    assert page.disk_label.text() == "Disk: —"
    fired = []
    page.actionRequested.connect(lambda a, i: fired.append((a, i)))
    page.btn_disk.click()
    assert fired == [("measure-disk", "x1")]
    page.set_disk_text("Disk: 1.2 GB")
    assert page.disk_label.text() == "Disk: 1.2 GB"
    page.show_instance(_inst_dict(status="stopped"))
    assert page.disk_label.text() == "Disk: —"


def test_overview_badges_and_fields(qapp, qtbot):
    page = OverviewPage()
    qtbot.addWidget(page)
    page.show()
    # Plaintext storage -> security box visible.
    d = _inst_dict(status="stopped")
    d["password_storage"] = "plaintext"
    page.show_instance(d)
    assert page.lbl_security.isVisible()
    assert "PLAINTEXT" in page.lbl_security.text()
    assert "Community edition." in page.ent_label.text()
    # CPU/memory rows render from poll dicts.
    d = _inst_dict(status="running")
    d.update(cpu_percent=12.6, memory_mb=512.4)
    page.refresh_status(d)
    assert page._fields["cpu"].text() == "13%"
    assert page._fields["memory"].text() == "512 MB"
    assert "Running" in page.status_label.text()


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


def test_flows_clone_unknown_instance_fails(qapp, qtbot):
    """U5.1: clone of a bogus id fails on the GUI thread, no worker."""
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.clone("no-such-id", "Whatever")
    assert messages == ["No instance with id 'no-such-id'"]


def test_flows_measure_disk_unknown_instance(qapp, qtbot):
    """U5.3: measure of a bogus id reports on the GUI thread, no worker."""
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.measure_disk("no-such-id")
    assert messages == ["No instance with id 'no-such-id'"]


def test_flows_measure_disk_reports(qapp, qtbot, tmp_path):
    """U5.3: real tmp folder -> diskReady with a human size."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    base = tmp_path / "du-inst"
    (base / "community").mkdir(parents=True)
    (base / "community" / "f").write_bytes(b"z" * 2048)
    inst = Instance(name="DuT", version="17.0", path=str(base),
                    community_path=str(base / "community"))
    assert create_instance(inst).ok
    flows = LifecycleFlows()
    ready = []
    flows.diskReady.connect(lambda iid, text: ready.append((iid, text)))
    flows.measure_disk(inst.id)
    qtbot.wait(3000)
    assert ready and ready[0][0] == inst.id
    assert ready[0][1].startswith("Disk: ")


def test_overview_venv_row_warns_and_fires(qapp, qtbot, tmp_path):
    """U5.2: rebuild row hidden when venv ok, warns + fires when missing."""
    page = OverviewPage()
    qtbot.addWidget(page)
    page.show()
    # Poll-style dict without venv fields: row keeps initial hidden state.
    page.show_instance(_inst_dict(status="stopped"))
    assert not page.lbl_venv.isVisible()
    assert not page.btn_venv.isVisible()
    # Missing venv python -> warn + offer rebuild.
    d = _inst_dict(status="stopped")
    d["venv_path"] = str(tmp_path / "novenv")
    page.show_instance(d)
    assert page.lbl_venv.isVisible()
    assert page.btn_venv.isVisible()
    assert "Rebuild" in page.lbl_venv.text() or "rebuild" in page.lbl_venv.text()
    fired = []
    page.actionRequested.connect(lambda a, i: fired.append((a, i)))
    page.btn_venv.click()
    assert fired == [("rebuild-venv", "x1")]
    # venv present -> row clears.
    py = tmp_path / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("stub")
    d["venv_path"] = str(tmp_path / "venv")
    page.show_instance(d)
    assert not page.lbl_venv.isVisible()
    # Busy cycle must not stick the button off (secure/venv regression).
    page.show_instance({**d, "venv_path": str(tmp_path / "novenv")})
    page.set_actions_enabled(False)
    assert not page.btn_venv.isEnabled()
    page.set_actions_enabled(True)
    assert page.btn_venv.isEnabled()


def test_flows_rebuild_unknown_instance(qapp, qtbot):
    """U5.2: rebuild dialog on a bogus id reports, no worker."""
    flows = LifecycleFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.rebuild_venv_dialog(None, "no-such-id")
    assert messages == ["No instance with id 'no-such-id'"]


@pytest.fixture(autouse=True)
def _drain_workers():
    """No QThread may outlive its test (teardown abort otherwise)."""
    yield
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background(), "background workers did not finish"


def test_busy_tracker_refcount(qapp, qtbot):
    from odoo_vite.ui_qt.workers import BusyTracker

    tracker = BusyTracker()
    changes = []
    tracker.changed.connect(changes.append)
    assert tracker.busy is False
    tracker.acquire()
    assert tracker.busy is True
    tracker.acquire()  # overlapping op: still busy, single transition
    assert changes == [True]
    tracker.release()
    assert tracker.busy is True  # one still outstanding
    assert changes == [True]
    tracker.release()
    assert tracker.busy is False
    assert changes == [True, False]
    tracker.release()  # never negative, no spurious signal
    assert tracker.busy is False
    assert changes == [True, False]


def test_run_in_background_drives_tracker(qapp, qtbot):
    import time

    from PySide6.QtCore import QObject

    from odoo_vite.core.result import Result
    from odoo_vite.ui_qt.workers import BusyTracker, run_in_background

    host = QObject()
    tracker = BusyTracker(host)
    host.busy_tracker = tracker
    changes = []
    tracker.changed.connect(changes.append)

    def _slow():
        time.sleep(1.0)
        return Result(ok=True, message="done", data={})

    def _fail():
        return Result.failure("nope")

    run_in_background(host, _slow, lambda *a: None)
    run_in_background(host, _fail, lambda *a: None)
    qtbot.wait(300)
    assert tracker.busy is True  # overlapping: still busy
    assert changes == [True]
    qtbot.wait(3000)
    assert tracker.busy is False  # both landed, incl. failure path
    assert changes == [True, False]


def test_quiet_workers_skip_tracker(qapp, qtbot):
    from PySide6.QtCore import QObject

    from odoo_vite.core.result import Result
    from odoo_vite.ui_qt.workers import BusyTracker, run_in_background

    host = QObject()
    tracker = BusyTracker(host)
    host.busy_tracker = tracker
    changes = []
    tracker.changed.connect(changes.append)
    run_in_background(host, lambda: Result(ok=True, message="x", data={}),
                      lambda *a: None, quiet=True)
    qtbot.wait(1500)
    assert changes == []
    assert tracker.busy is False
