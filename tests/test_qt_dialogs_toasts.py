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
