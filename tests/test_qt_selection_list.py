"""PSQ-2.1: SelectionList behavior (offscreen, pytest-qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from PySide6.QtCore import Qt  # noqa: E402

from odoo_vite.ui_qt.widgets.selection_list import SelectionList  # noqa: E402


def _rows():
    return [
        {"id": "a", "title": "Likely One", "badge": "17.0",
         "group": "Likely", "checked": True},
        {"id": "b", "title": "Likely Two", "badge": "", "group": "Likely"},
        {"id": "c", "title": "Other One", "badge": "16.0", "group": "Other"},
    ]


def test_multi_check_and_filter(qapp, qtbot):
    w = SelectionList(multi=True)
    qtbot.addWidget(w)
    w.set_items(_rows())
    assert w.checked_ids() == ["a"]
    # header rows present but not selectable/checkable
    assert w.visible_count() == 5  # 3 rows + 2 group headers
    assert w.set_checked("b", True)
    assert sorted(w.checked_ids()) == ["a", "b"]
    w.filter_edit.setText("other")
    assert w.visible_count() == 2  # "Other" header + "Other One"
    w.filter_edit.setText("")
    assert w.visible_count() == 5


def test_group_headers_not_checkable(qapp, qtbot):
    w = SelectionList(multi=True)
    qtbot.addWidget(w)
    w.set_items(_rows())
    assert not w.set_checked("__group:Likely", True)
    assert w.checked_ids() == ["a"]


def test_single_select_mode(qapp, qtbot):
    w = SelectionList(multi=False, searchable=False)
    qtbot.addWidget(w)
    w.set_items(_rows())
    assert not w.filter_edit.isVisible()
    w.view.setCurrentIndex(w.view.model().index(1, 0))
    assert w.selected_id() == "a"
    # Space must NOT toggle anything in single mode
    qtbot.keyClick(w.view.viewport(), Qt.Key_Space)
    assert w.checked_ids() == []


def test_space_toggles_checkbox(qapp, qtbot):
    w = SelectionList(multi=True)
    qtbot.addWidget(w)
    w.set_items(_rows())
    w.show()
    model = w.view.model()
    w.view.setCurrentIndex(model.index(2, 0))  # row "b"
    assert w.view.currentIndex().data(Qt.CheckStateRole) == Qt.Unchecked
    qtbot.keyClick(w.view.viewport(), Qt.Key_Space)
    assert w.view.currentIndex().data(Qt.CheckStateRole) == Qt.Checked
    assert sorted(w.checked_ids()) == ["a", "b"]


def test_height_cap_applied(qapp, qtbot):
    w = SelectionList(multi=True, max_visible_rows=4)
    qtbot.addWidget(w)
    w.set_items([{"id": i, "title": f"row {i}"} for i in range(100)])
    assert w.view.maximumHeight() <= 4 * 40 + 8
    assert w.view.maximumHeight() >= 4 * 24


def test_type_to_search_focuses_filter(qapp, qtbot):
    w = SelectionList(multi=True)
    qtbot.addWidget(w)
    w.show()
    w.set_items(_rows())
    w.view.setFocus()
    qtbot.keyClick(w.view.viewport(), "o")
    # Routing is what matters (offscreen never grants real focus, so
    # hasFocus() is unassertable here — text arrival proves the path).
    assert w.filter_edit.text() == "o"
