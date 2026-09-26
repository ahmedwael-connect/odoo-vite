"""Shared selection component (PSQ-2.1): single/multi, grouped, filterable.

One component for every selection UI the app needs (Discover tracking,
Module Install multi-pick, database switcher, record lists, cron lists):
model/view based (QListView virtualizes — safe for 1500+ rows), with a
built-in filter entry, a height cap, and a checked state that is
unambiguous without hovering (bold title + accent + real checkbox),
per the GTK bug record.

Row dicts: {"id": hashable, "title": str, "badge": str = "",
"group": str = "", "checked": bool = ""}.
Groups render as non-selectable header rows in first-seen order.
"""

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QListView,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

_TITLE_ROLE = Qt.UserRole + 1
_BADGE_ROLE = Qt.UserRole + 2
_ID_ROLE = Qt.UserRole + 3
_HEADER_ROLE = Qt.UserRole + 4


class _RowModel(QAbstractListModel):
    def __init__(self, multi: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._multi = multi
        self._rows: list[dict] = []  # full set; filtering via _visible
        self._visible: list[int] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = [dict(r) for r in rows]
        if not self._multi:
            for row in self._rows:
                row["checked"] = False
        self._visible = list(range(len(self._rows)))
        self.endResetModel()

    def apply_filter(self, needle: str) -> None:
        needle = (needle or "").strip().lower()
        self.beginResetModel()
        self._visible = [
            i for i, r in enumerate(self._rows)
            if not needle or needle in str(r.get("title", "")).lower()
            or needle in str(r.get("badge", "")).lower()
        ]
        self.endResetModel()

    def source_index(self, visible_row: int) -> int:
        return self._visible[visible_row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._visible)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        row = self._rows[self._visible[index.row()]]
        if row.get("header"):
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._multi:
            base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[self._visible[index.row()]]
        if role == _TITLE_ROLE:
            return row.get("title", "")
        if role == _BADGE_ROLE:
            return row.get("badge", "")
        if role == _ID_ROLE:
            return row.get("id")
        if role == _HEADER_ROLE:
            return bool(row.get("header"))
        if role == Qt.ToolTipRole and not row.get("header"):
            tip = str(row.get("title", ""))
            if row.get("badge"):
                tip += f" — {row['badge']}"
            return tip
        if role == Qt.CheckStateRole and self._multi and not row.get("header"):
            return (Qt.Checked if row.get("checked")
                    else Qt.Unchecked)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:  # noqa: N802
        if (not index.isValid() or role != Qt.CheckStateRole
                or not self._multi):
            return False
        row = self._rows[self._visible[index.row()]]
        if row.get("header"):
            return False
        # NOTE: int(Qt.Unchecked) raises TypeError on PySide6 (enums are
        # not int()-convertible), and bool(Qt.Unchecked) is True — so a
        # naive conversion makes every uncheck a no-op. Compare as enum.
        if isinstance(value, bool):
            row["checked"] = value
        else:
            try:
                row["checked"] = (Qt.CheckState(value) == Qt.Checked)
            except (TypeError, ValueError):
                row["checked"] = False
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        return True

    def set_checked_id(self, item_id, checked: bool) -> bool:
        for position, source in enumerate(self._visible):
            if self._rows[source].get("id") == item_id:
                idx = self.index(position)
                return self.setData(idx, Qt.Checked if checked else Qt.Unchecked,
                                    Qt.CheckStateRole)
        return False

    def checked_ids(self) -> list:
        return [r["id"] for r in self._rows
                if r.get("checked") and not r.get("header")]


class _RowDelegate(QStyledItemDelegate):
    """Checkbox + bold-when-checked title + dim badge, elided titles."""

    def __init__(self, multi: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._multi = multi

    def paint(self, painter, option, index) -> None:
        if index.data(_HEADER_ROLE):
            opt = QStyleOptionButton()
            opt.rect = option.rect
            opt.text = str(index.data(_TITLE_ROLE) or "")
            opt.state = option.state
            QApplication.style().drawControl(
                QStyle.CE_ItemViewItem, option, painter)
            painter.save()
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect.adjusted(8, 0, -8, 0),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             opt.text)
            painter.restore()
            return
        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        # Base row (selection highlight, focus) from the style first.
        super().paint(painter, option, index)
        rect = option.rect
        x = rect.x() + 8
        multi = self._multi
        if multi:
            box = QStyleOptionButton()
            box.rect = option.rect
            box.rect.setX(x)
            box.rect.setWidth(24)
            box.state = (QStyle.State_On if checked else QStyle.State_Off)
            box.state |= QStyle.State_Enabled
            QApplication.style().drawPrimitive(
                QStyle.PE_IndicatorCheckBox, box, painter)
            x += 28
        painter.save()
        if checked:
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(option.palette.highlight().color())
        metrics = QFontMetrics(painter.font())
        title = metrics.elidedText(str(index.data(_TITLE_ROLE) or ""),
                                   Qt.ElideMiddle, max(40, rect.width() - 120))
        badge = str(index.data(_BADGE_ROLE) or "")
        painter.drawText(x, rect.y(), max(40, rect.width() - 120),
                         rect.height(), Qt.AlignLeft | Qt.AlignVCenter, title)
        if badge:
            painter.restore()
            painter.save()
            # Fixed gray, not palette.mid() (resolves to white in bare
            # environments — same lesson as qt_style.qss).
            painter.setPen(QColor("#888888"))
            painter.drawText(x, rect.y(), rect.width() - 8, rect.height(),
                             Qt.AlignRight | Qt.AlignVCenter,
                             metrics.elidedText(badge, Qt.ElideRight, 140))
        painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtCore import QSize

        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), 34))


class SelectionList(QWidget):
    """Reusable selection widget. See module docstring for the contract."""

    checkedChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, multi: bool = True, searchable: bool = True,
                 max_visible_rows: int = 12,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._multi = multi
        self._max_visible_rows = max(3, max_visible_rows)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Filter…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setVisible(searchable)
        layout.addWidget(self.filter_edit)
        self._model = _RowModel(multi, self)
        self.view = QListView(self)
        self.view.setModel(self._model)
        delegate = _RowDelegate(multi, self)
        self.view.setItemDelegate(delegate)
        self.view.setUniformItemSizes(True)
        if not multi:
            self.view.setSelectionMode(QListView.SingleSelection)
        else:
            self.view.setSelectionMode(QListView.NoSelection)
        layout.addWidget(self.view)
        self.filter_edit.textChanged.connect(self._on_filter)
        self._model.dataChanged.connect(lambda *_: self.checkedChanged.emit())
        self._model.modelReset.connect(lambda: self._apply_height_cap())
        self.view.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit())
        # Key events land on the viewport (focus proxy), not the view.
        self.view.viewport().installEventFilter(self)
        self._apply_height_cap()

    # ------------------------------------------------------------------ API

    def set_items(self, rows: list[dict]) -> None:
        """Rows as documented in the module docstring; groups ordered."""
        flat: list[dict] = []
        seen_groups: list[str] = []
        by_group: dict[str, list[dict]] = {}
        ungrouped: list[dict] = []
        for row in rows:
            group = str(row.get("group") or "")
            if group:
                if group not in by_group:
                    by_group[group] = []
                    seen_groups.append(group)
                by_group[group].append(row)
            else:
                ungrouped.append(row)
        for row in ungrouped:
            flat.append(row)
        for group in seen_groups:
            flat.append({"id": f"__group:{group}", "title": group,
                         "header": True})
            flat.extend(by_group[group])
        self._model.set_rows(flat)

    def checked_ids(self) -> list:
        return self._model.checked_ids()

    def set_checked(self, item_id, checked: bool) -> bool:
        ok = self._model.set_checked_id(item_id, checked)
        if ok:
            self.checkedChanged.emit()
        return ok

    def selected_id(self):
        indexes = self.view.selectionModel().selectedIndexes()
        if not indexes:
            return None
        return indexes[0].data(_ID_ROLE)

    def current_id(self):
        """Keyboard/current row id — works in any selection mode
        (selection model is NoSelection in multi mode)."""
        index = self.view.currentIndex()
        if not index.isValid() or index.data(_HEADER_ROLE):
            return None
        return index.data(_ID_ROLE)

    def select_id(self, item_id) -> bool:
        """Select a row by id (headers never match). Returns found."""
        model = self.view.model()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if index.data(_HEADER_ROLE):
                continue
            if index.data(_ID_ROLE) == item_id:
                self.view.setCurrentIndex(index)
                return True
        return False

    def visible_count(self) -> int:
        return self._model.rowCount()

    # -------------------------------------------------------------- internals

    @Slot(str)
    def _on_filter(self, text: str) -> None:
        self._model.apply_filter(text)

    def _apply_height_cap(self) -> None:
        row_h = 34
        try:
            row_h = max(24, self.view.sizeHintForRow(0))
        except Exception:
            pass
        margins = 4
        self.view.setMaximumHeight(row_h * self._max_visible_rows + margins)
        self.view.setMinimumHeight(min(row_h * 3 + margins,
                                       row_h * self._max_visible_rows + margins))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.view.viewport() and event.type() == QEvent.KeyPress:
            key_event: QKeyEvent = event
            if (self._multi and key_event.key() in (Qt.Key_Space,)
                    and not key_event.modifiers()):
                index = self.view.currentIndex()
                if index.isValid() and not index.data(_HEADER_ROLE):
                    current = index.data(Qt.CheckStateRole) == Qt.Checked
                    self._model.setData(
                        index,
                        Qt.Unchecked if current else Qt.Checked,
                        Qt.CheckStateRole)
                    self.checkedChanged.emit()
                    return True
            if (key_event.text().isprintable() and self.filter_edit.isVisible()
                    and not key_event.modifiers()):
                self.filter_edit.setFocus()
                self.filter_edit.insert(key_event.text())
                return True
        return super().eventFilter(watched, event)
