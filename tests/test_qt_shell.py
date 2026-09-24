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


def test_qt_shell_boots_with_real_instances(qapp):
    load_qt_style(qapp)
    assert qapp.styleSheet().strip(), "QSS did not apply"
    win = QtMainWindow()
    win.show()
    expected = list_instances()
    rows = win.sidebar._list.visible_count()
    assert rows == max(len(expected), 1)
    if expected:
        assert win.sidebar._list.select_id(expected[0].id)
        assert win.sidebar.selected_id() == expected[0].id
    assert win.stack.count() >= 1
    win.close()


def test_qt_style_qss_parses(qapp):
    from pathlib import Path

    qss = (Path(__file__).resolve().parents[1]
           / "odoo_vite" / "ui_qt" / "qt_style.qss").read_text()
    qapp.setStyleSheet(qss)  # must not warn/crash; smoke-level check
    assert "palette(highlight)" in qss
    assert "QListWidget::item:selected" in qss
