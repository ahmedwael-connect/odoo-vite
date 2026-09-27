"""U2.1: Preferences dialog + provisioning-mode wiring (offscreen, pytest-qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from odoo_vite.ui_qt.widgets.preferences import PreferencesDialog  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Qt tests never touch the real registry (keyring/dbus abort risk)."""
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-prefs.db"))


def test_dialog_defaults_and_selection(qapp, qtbot):
    dlg = PreferencesDialog(None, "developer", True, "/tmp/x.db")
    qtbot.addWidget(dlg)
    assert dlg.selected_mode() == "developer"
    dlg.radio_managed.setChecked(True)
    assert dlg.selected_mode() == "managed"
    dlg.close()


def test_dialog_honours_managed_current(qapp, qtbot):
    dlg = PreferencesDialog(None, "managed", False, "")
    qtbot.addWidget(dlg)
    assert dlg.radio_managed.isChecked()
    assert dlg.selected_mode() == "managed"
    dlg.close()


def test_settings_round_trip_isolated():
    from odoo_vite.core.settings import (
        get_provisioning_mode,
        set_provisioning_mode,
    )

    assert get_provisioning_mode() == "developer"
    assert set_provisioning_mode("managed").ok
    assert get_provisioning_mode() == "managed"
    assert set_provisioning_mode("developer").ok
    assert get_provisioning_mode() == "developer"


def test_main_window_preferences_saves(qapp, qtbot):
    from odoo_vite.core.settings import get_provisioning_mode
    from odoo_vite.ui_qt.main_window import QtMainWindow

    assert get_provisioning_mode() == "developer"
    win = QtMainWindow()
    qtbot.addWidget(win)
    win.show()
    assert hasattr(win, "btn_prefs")

    def _pick_managed_and_accept():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, PreferencesDialog) and w.isVisible():
                w.radio_managed.setChecked(True)
                w.accept()
                return

    QTimer.singleShot(400, _pick_managed_and_accept)
    win._open_preferences()
    assert get_provisioning_mode() == "managed"
    win._poll.stop()
    from odoo_vite.ui_qt.workers import wait_for_background
    assert wait_for_background()
    win.close()
