"""Instance sidebar (PSQ-3.2): single-select SelectionList of instances.

First dogfood of the shared component: instance rows are exactly the
single-select case (title = name, badge = live status). Refresh rebuilds
rows but preserves selection by id. Emits instanceSelected(id) — the main
window shows the Overview page for it.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from odoo_vite.ui_qt.widgets.selection_list import SelectionList


class InstanceSidebar(QWidget):
    instanceSelected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = SelectionList(multi=False, searchable=False,
                                   max_visible_rows=30, parent=self)
        layout.addWidget(self._list)
        self._list.selectionChanged.connect(self._on_selection)

    def set_instances(self, instances: list) -> None:
        """instances: core Instance rows (or dicts with id/name/status)."""
        current = self.selected_id()
        rows = []
        for inst in instances:
            if isinstance(inst, dict):
                iid, name, status = (inst.get("id", ""), inst.get("name", "?"),
                                     inst.get("status", ""))
            else:
                iid, name, status = (inst.id, inst.name, inst.status or "")
            rows.append({"id": iid, "title": str(name),
                         "badge": str(status).capitalize()})
        self._list.set_items(rows)
        if current:
            self._list.select_id(current)

    def selected_id(self):
        return self._list.selected_id()

    def _on_selection(self) -> None:
        item_id = self.selected_id()
        if item_id:
            self.instanceSelected.emit(item_id)
