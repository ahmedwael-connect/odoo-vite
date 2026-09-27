"""PSQ-2.2: dialog patterns + toasts (offscreen, pytest-qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QWidget,
)

from odoo_vite.ui_qt.widgets.dialogs import (  # noqa: E402
    _base_dialog,
    ask_confirm,
    ask_confirm_typed,
)
from odoo_vite.ui_qt.widgets.toasts import Toaster  # noqa: E402


def _close_next_top_dialog(result: int):
    def _fire():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                w.done(result)
                return
    QTimer.singleShot(300, _fire)


def test_ask_confirm_accepts(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    _close_next_top_dialog(QDialog.Accepted)
    assert ask_confirm(host, "Start?", "Start the instance?",
                       "Start") is True


def test_ask_confirm_rejects(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    _close_next_top_dialog(QDialog.Rejected)
    assert ask_confirm(host, "Remove?", destructive=True) is False


def test_typed_dialog_structure(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    dlg, buttons = _base_dialog(host, "Drop?", "Drop Figs?", "Delete", True)
    qtbot.addWidget(dlg)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    assert ok_btn.text() == "Delete"
    assert ok_btn.property("role") == "destructive"
    dlg.close()


def test_typed_confirm_end_to_end(qapp, qtbot):
    """Full ask_confirm_typed with matching entry → True."""
    host = QWidget()
    qtbot.addWidget(host)
    host.show()

    def _fill_and_accept():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                entry = w.findChild(QLineEdit)
                if entry is not None:
                    entry.setText("figs")
                w.done(QDialog.Accepted)
                return
    # NOTE: done() bypasses the disabled-OK gate by construction; this
    # proves plumbing only. The gate rule is proven by test_typed_gate_logic.
    QTimer.singleShot(300, _fill_and_accept)
    assert ask_confirm_typed(host, "Drop?", "Drop Figs?", "figs") is True


def test_typed_gate_logic():
    """The exact-match rule, no GUI needed."""
    from odoo_vite.ui_qt.widgets.dialogs import typed_gate_ok

    assert typed_gate_ok("figs", "figs")
    assert typed_gate_ok("  figs  ", "figs")
    assert not typed_gate_ok("fig", "figs")
    assert not typed_gate_ok("", "figs")
    assert not typed_gate_ok("FIGS", "figs")


def test_toast_shows_and_expires(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(400, 300)
    host.show()
    toast = Toaster.show_text(host, "Saved", timeout_ms=200)
    assert toast.isVisible()
    assert toast.text() == "Saved"
    qtbot.wait(500)
    import shiboken6

    assert not shiboken6.isValid(toast)  # expired + deleteLater ran


def test_progress_dialog_cancel_and_done(qapp, qtbot):
    """S2: Cancel sets the worker event; done locks both buttons."""
    from odoo_vite.ui_qt.widgets.progress_dialog import ProgressDialog

    host = QWidget()
    qtbot.addWidget(host)
    dlg = ProgressDialog(host, "Rebuilding venv — T")
    qtbot.addWidget(dlg)
    assert not dlg.cancel_event.is_set()
    assert dlg.cancel_btn.isEnabled()
    assert not dlg.close_btn.isEnabled()
    dlg.cancel_btn.click()
    assert dlg.cancel_event.is_set()
    assert not dlg.cancel_btn.isEnabled()
    assert "Cancelling" in dlg.status_label.text()
    dlg.request_done.emit(False, "cancelled by test")
    assert dlg.close_btn.isEnabled()
    assert not dlg.cancel_btn.isEnabled()
    dlg.close()


def test_toasts_coalesce_per_host(qapp, qtbot):
    """S4: rapid messages update one toast instead of stacking."""
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(400, 300)
    host.show()
    first = Toaster.show_text(host, "one", timeout_ms=600)
    second = Toaster.show_text(host, "two", timeout_ms=600)
    assert second is first
    assert first.text() == "two"
    assert len(Toaster._live) == 1
    qtbot.wait(900)
    import shiboken6

    assert not shiboken6.isValid(first)


def test_button_icons_and_hierarchy(qapp, qtbot):
    from PySide6.QtWidgets import QPushButton

    from odoo_vite.ui_qt.widgets.icons import ICONS, style_button

    assert set(ICONS) >= {"start", "stop", "restart", "remove", "save",
                          "add", "connect", "run", "apply", "delete"}
    btn = QPushButton("Install Checked")
    qtbot.addWidget(btn)
    style_button(btn, "run", primary=True)
    assert not btn.icon().isNull()
    assert btn.isDefault()
    plain = QPushButton("Cancel")
    qtbot.addWidget(plain)
    style_button(plain, "no-such-icon")
    assert plain.icon().isNull()
    assert not plain.isDefault()


def test_toolbar_icons_mapped(qapp, qtbot):
    """S5: Adopt/Events toolbar buttons resolve to real style icons."""
    from PySide6.QtWidgets import QPushButton

    from odoo_vite.ui_qt.widgets.icons import ICONS, style_button

    assert set(ICONS) >= {"adopt", "events"}
    for name in ("adopt", "events", "new"):
        btn = QPushButton(name)
        qtbot.addWidget(btn)
        style_button(btn, name)
        assert not btn.icon().isNull(), f"toolbar icon missing: {name}"
    # Unknown names stay icon-less and never raise.
    bare = QPushButton("Preferences")
    qtbot.addWidget(bare)
    style_button(bare, "preferences")
    assert bare.icon().isNull()
