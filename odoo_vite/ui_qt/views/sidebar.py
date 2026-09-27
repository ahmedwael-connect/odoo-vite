"""Instance sidebar (PSQ-3.2): single-select SelectionList of instances.

First dogfood of the shared component: instance rows are exactly the
single-select case (title = name, badge = live status). Refresh rebuilds
rows but preserves selection by id. Emits instanceSelected(id) — the main
window shows the Overview page for it.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from odoo_vite.ui_qt.widgets.selection_list import SelectionList


class InstanceSidebar(QWidget):
    instanceSelected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_emitted: str | None = None
        self._last_rows: tuple = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = SelectionList(multi=False, searchable=False,
                                   max_visible_rows=30, parent=self)
        layout.addWidget(self._list)
        self.lbl_empty = QLabel("No instances yet — create or adopt one.")
        self.lbl_empty.setProperty("class", "dim")
        self.lbl_empty.setWordWrap(True)
        layout.addWidget(self.lbl_empty)
        self._list.selectionChanged.connect(self._on_selection)

    def set_instances(self, instances: list) -> None:
        """instances: core Instance rows (or dicts with id/name/status).

        Diff-before-repaint: identical input skips the model reset, so the
        2s poll doesn't flicker the highlight or steal an in-flight click.
        """
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
        fingerprint = tuple((r["id"], r["title"], r["badge"]) for r in rows)
        if fingerprint == self._last_rows:
            return
        self._last_rows = fingerprint
        self._list.set_items(rows)
        self.lbl_empty.setVisible(len(rows) == 0)
        if current:
            self._list.select_id(current)

    def selected_id(self):
        return self._list.selected_id()

    def _on_selection(self) -> None:
        # Restoring a highlight after a model reset re-fires selectionChanged
        # for the same row — only real changes reach the main window, or
        # every poll would reload all pages + respawn workers.
        item_id = self.selected_id()
        if item_id != self._last_emitted:
            self._last_emitted = item_id
            if item_id:
                self.instanceSelected.emit(item_id)
