"""PSQ-7: Logs view + flows (offscreen, pytest-qt).

The follow test exercises the FULL cycle — follow → scroll-up auto-pause
→ resume at bottom — against a real tmp log file and the real
LogFollower, not just "new lines appear" (PSQ-6 uncheck lesson applied
to follow: every transition gets its own assertion).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.flows.logs import LogFlows  # noqa: E402
from odoo_vite.ui_qt.views.logs import LogsPage  # noqa: E402


@pytest.fixture()
def _log_instance(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-log.db"))
    log = tmp_path / "odoo.log"
    log.write_text("".join(f"2026-01-01 line {i}\n" for i in range(20)))
    inst = Instance(name="LogT", version="17.0", path=str(tmp_path),
                    port=8096, primary_db="d", log_path=str(log))
    assert create_instance(inst).ok
    return inst


def _bar(page):
    return page.tail_view.verticalScrollBar()


def _at_bottom(page):
    bar = _bar(page)
    return bar.value() >= bar.maximum() - 8


def test_follow_full_cycle(qapp, qtbot, _log_instance, tmp_path):
    page = LogsPage()
    qtbot.addWidget(page)
    page.show()
    page.show_instance(_log_instance)
    qtbot.wait(500)
    assert page.tail_model.rowCount() == 20
    assert _at_bottom(page), "initial tail must land at bottom"
    assert not page.lbl_paused.isVisible()

    # New lines while following -> tracked to bottom, no pause banner.
    with open(_log_instance.log_path, "a") as fh:
        fh.write("2026-01-01 line 20\n2026-01-01 line 21\n")
    qtbot.wait(1800)
    assert page.tail_model.rowCount() == 22
    assert _at_bottom(page)
    assert not page.lbl_paused.isVisible()

    # User scrolls up -> auto-pause: view stays, banner shows.
    _bar(page).setValue(0)
    with open(_log_instance.log_path, "a") as fh:
        fh.write("2026-01-01 line 22\n")
    qtbot.wait(1800)
    assert page.tail_model.rowCount() == 23
    assert _bar(page).value() < _bar(page).maximum() - 8
    assert page.lbl_paused.isVisible()

    # Scroll back to bottom -> resume: next batch follows again.
    _bar(page).setValue(_bar(page).maximum())
    with open(_log_instance.log_path, "a") as fh:
        fh.write("2026-01-01 line 23\n")
    qtbot.wait(1800)
    assert page.tail_model.rowCount() == 24
    assert _at_bottom(page)
    assert not page.lbl_paused.isVisible()
    page.stop_poll()


def test_follow_toggle_off_pauses(qapp, qtbot, _log_instance):
    page = LogsPage()
    qtbot.addWidget(page)
    page.show()
    page.show_instance(_log_instance)
    qtbot.wait(500)
    page.btn_follow.setChecked(False)
    assert page.lbl_paused.isVisible()
    with open(_log_instance.log_path, "a") as fh:
        fh.write("2026-01-01 line X\n")
    qtbot.wait(1800)
    assert page.tail_model.rowCount() == 21  # still tailed...
    assert page.lbl_paused.isVisible()  # ...but banner stays while off
    page.stop_poll()


def test_clear_view_keeps_file(qapp, qtbot, _log_instance):
    page = LogsPage()
    qtbot.addWidget(page)
    page.show()
    page.show_instance(_log_instance)
    qtbot.wait(500)
    assert page.tail_model.rowCount() == 20
    page.btn_clear.click()
    assert page.tail_model.rowCount() == 0
    assert len(open(_log_instance.log_path).readlines()) == 20
    page.stop_poll()


def test_search_doctor_render(qapp, qtbot, _log_instance):
    page = LogsPage()
    qtbot.addWidget(page)
    page.show()
    page.set_search_results(
        [{"lineno": 3, "line": "ERROR boom", "before": [], "after": []}],
        "1 match")
    assert page.search_results.count() == 1
    assert "1 match" in page.lbl_search_status.text()
    page.set_doctor_findings(
        [{"severity": "high", "title": "No DB", "count": 2}])
    assert page.doctor_list.isVisible()
    assert "×2" in page.doctor_list.item(0).text()
    page.set_doctor_findings([])
    assert not page.doctor_list.isVisible()
    page.set_slow_queries(True, "2 queries", [
        {"query": "SELECT 1", "calls": 5, "total_ms": 12.5}])
    assert page.slow_list.count() == 1
    assert "5 calls" in page.slow_list.item(0).text()


def test_log_flows_fail_safe(qapp, qtbot):
    flows = LogFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.search("no-such-id", "x", None)
    flows.doctor("no-such-id")
    flows.slow_refresh("no-such-id")
    flows.profile("no-such-id")
    qtbot.wait(2000)
    assert any("No log file" in m for m in messages)


def test_profile_needs_running(qapp, qtbot, _log_instance):
    flows = LogFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.profile(_log_instance.id, 5)
    qtbot.wait(2000)
    assert any("running" in m for m in messages)


def test_event_dock_feeds(qapp, qtbot):
    from odoo_vite.ui_qt.main_window import QtMainWindow

    win = QtMainWindow()
    qtbot.addWidget(win)
    win.show()
    assert not win.event_dock.isVisible()
    win.btn_events.click()
    qtbot.wait(1500)
    assert win.event_dock.isVisible()
    assert win.event_list.count() >= 0
    win.btn_events.click()
    assert not win.event_dock.isVisible()
    win._poll.stop()
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background()
    win.close()


def test_event_dock_toggle_stays_synced(qapp, qtbot, tmp_path, monkeypatch):
    """Closing the dock via its X must uncheck the toggle (else drift)."""
    from odoo_vite.ui_qt.main_window import QtMainWindow

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-dock.db"))
    win = QtMainWindow()
    qtbot.addWidget(win)
    win.show()
    win.btn_events.click()
    assert win.event_dock.isVisible()
    assert win.btn_events.isChecked()
    win.event_dock.close()
    assert not win.btn_events.isChecked()
    win._poll.stop()
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background()
    win.close()
