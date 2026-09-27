"""S1: SelectionList UX — filter/focus retention, Space, count label."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from PySide6.QtCore import Qt  # noqa: E402

from odoo_vite.ui_qt.widgets.selection_list import SelectionList  # noqa: E402


def _rows():
    return [
        {"id": "a", "title": "Alpha"},
        {"id": "b", "title": "Beta"},
        {"id": "c", "title": "Gamma"},
    ]


def test_filter_survives_refresh(qapp, qtbot):
    lst = SelectionList(multi=True)
    qtbot.addWidget(lst)
    lst.show()
    lst.set_items(_rows())
    lst.filter_edit.setText("alp")
    assert lst.visible_count() == 1
    lst.set_items(_rows() + [{"id": "d", "title": "Alpine"}])
    assert lst.filter_edit.text() == "alp", "refresh must not clear typing"
    assert lst.visible_count() == 2


def test_keyboard_position_survives_refresh(qapp, qtbot):
    lst = SelectionList(multi=False, searchable=False)
    qtbot.addWidget(lst)
    lst.show()
    lst.set_items(_rows())
    assert lst.select_id("b")
    assert lst.current_id() == "b"
    lst.set_items(_rows())
    assert lst.current_id() == "b", "cursor must not jump on refresh"


def test_space_selects_single_row(qapp, qtbot):
    from PySide6.QtCore import QItemSelectionModel

    lst = SelectionList(multi=False, searchable=False)
    qtbot.addWidget(lst)
    lst.show()
    lst.set_items(_rows())
    assert lst.select_id("a")
    # Cursor on "c", selection still on "a" (arrow-key state).
    lst.view.selectionModel().setCurrentIndex(
        lst.view.model().index(2, 0), QItemSelectionModel.NoUpdate)
    assert lst.current_id() == "c"
    assert lst.selected_id() == "a"
    picked = []
    lst.selectionChanged.connect(lambda: picked.append(lst.selected_id()))
    qtbot.keyClick(lst.view.viewport(), Qt.Key_Space)
    assert picked == ["c"]


def test_count_label_multi_and_filtered(qapp, qtbot):
    lst = SelectionList(multi=True)
    qtbot.addWidget(lst)
    lst.show()
    lst.set_items(_rows())
    assert lst.count_label.isVisible()
    assert "0 checked" in lst.count_label.text()
    assert lst.set_checked("a", True)
    assert "1 checked" in lst.count_label.text()

    single = SelectionList(multi=False)
    qtbot.addWidget(single)
    single.show()
    single.set_items(_rows())
    assert not single.count_label.isVisible()
    single.filter_edit.setText("a")
    assert single.count_label.isVisible()
    assert "of 3 shown" in single.count_label.text()
