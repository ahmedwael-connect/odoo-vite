"""PSQ-1.6: Qt shell boots headlessly (offscreen) with real registry data.

Runs under QT_QPA_PLATFORM=offscreen (set below if absent) — no display.
Uses pytest-qt's qapp fixture; qtbot available for later sprints.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.core.registry import list_instances  # noqa: E402
from odoo_vite.ui_qt import load_qt_style  # noqa: E402
from odoo_vite.ui_qt.main_window import QtMainWindow  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Qt tests never touch the real registry (keyring/dbus abort risk)."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-shell.db"))
    # NOTE: no tracked DBs on purpose — refresh_states would otherwise run
    # real psql in worker threads mid-suite (abort observed); DB-touching
    # worker paths are proven by the live E2E, not unit tests.
    assert create_instance(Instance(
        name="Qt One", version="17.0", path="/tmp/qt-one", port=8091,
        primary_db="", tracked_dbs=[])).ok
    assert create_instance(Instance(
        name="Qt Two", version="17.0", path="/tmp/qt-two", port=8092,
        primary_db="", tracked_dbs=[])).ok


def test_qt_shell_boots_with_real_instances(qapp, qtbot):
    load_qt_style(qapp)
    assert qapp.styleSheet().strip(), "QSS did not apply"
    win = QtMainWindow()
    # qtbot MUST own teardown: without it the 2s poll timer outlives the
    # test and fires real-registry workers into unrelated later tests
    # (verified abort via keyring/dbus from a leaked poll tick).
    qtbot.addWidget(win)
    win.show()
    expected = list_instances()
    rows = win.sidebar._list.visible_count()
    assert rows == max(len(expected), 1)
    if expected:
        assert win.sidebar._list.select_id(expected[0].id)
        assert win.sidebar.selected_id() == expected[0].id
    assert win.stack.count() >= 1
    # Quiesce BEFORE qtbot teardown: destroying the window with a poll
    # worker in flight aborts (QThread destroyed while running). The
    # autouse drain is only a backstop — teardown order would run it after
    # qtbot already destroyed the window.
    win._poll.stop()
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background()
    win.close()


def test_qt_style_qss_parses(qapp):
    from pathlib import Path

    qss = (Path(__file__).resolve().parents[1]
           / "odoo_vite" / "ui_qt" / "qt_style.qss").read_text()
    qapp.setStyleSheet(qss)  # must not warn/crash; smoke-level check
    assert "palette(highlight)" in qss
    assert "QListWidget::item:selected" in qss


@pytest.fixture(autouse=True)
def _drain_workers():
    """No QThread may outlive its test (teardown abort otherwise)."""
    yield
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background(), "background workers did not finish"
