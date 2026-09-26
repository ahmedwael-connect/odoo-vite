"""Databases page (PSQ-4.2, Part B redesign): grouped sections + columns.

Switch/database management/operations live in bordered QGroupBox
sections. Tracked databases render as a real columned tree (Database,
Size, Version, Status) with ★ primary marker and initialized coloring,
not concatenated text lines. Empty list shows an explicit empty state.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402


class DatabasesPage(QWidget):
    actionRequested = Signal(str, str, object)  # (action, instance_id, payload)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._busy = False
        self._primary_db: str = ""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 12, 16, 16)

        switch_box = QGroupBox("Switch database")
        switch_layout = QVBoxLayout(switch_box)
        switch_layout.setSpacing(8)
        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)
        self.db_combo = QComboBox()
        self.db_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        switch_row.addWidget(self.db_combo, 1)
        self.btn_set_primary = QPushButton("Set as Primary")
        self.btn_set_primary.setToolTip("Use for future starts (no restart)")
        self.btn_set_primary.clicked.connect(
            lambda: self._emit("set-primary", self._selected_db()))
        switch_row.addWidget(self.btn_set_primary)
        self.btn_switch = QPushButton("Switch Now")
        self.btn_switch.setToolTip("Restart the running instance on this DB")
        self.btn_switch.clicked.connect(
            lambda: self._emit("switch", self._selected_db()))
        switch_row.addWidget(self.btn_switch)
        switch_layout.addLayout(switch_row)
        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        manual_row.addWidget(QLabel("Database name:"))
        self.entry_manual_db = QLineEdit()
        self.entry_manual_db.setPlaceholderText("Add by name…")
        manual_row.addWidget(self.entry_manual_db, 1)
        btn_add = style_button(QPushButton("Track"), "add")
        btn_add.clicked.connect(self._on_track_manual)
        manual_row.addWidget(btn_add)
        switch_layout.addLayout(manual_row)
        layout.addWidget(switch_box)

        tracked_box = QGroupBox("Tracked databases")
        tracked_layout = QVBoxLayout(tracked_box)
        tracked_layout.setSpacing(8)
        self.tracked_list = QTreeWidget()
        self.tracked_list.setHeaderLabels(
            ["Database", "Size", "Version", "Status"])
        self.tracked_list.setRootIsDecorated(False)
        self.tracked_list.setSelectionMode(
            self.tracked_list.SelectionMode.SingleSelection)
        self.tracked_list.currentItemChanged.connect(
            lambda *_: self._sync_row_buttons())
        tracked_layout.addWidget(self.tracked_list)
        self.lbl_tracked_empty = QLabel(
            "No tracked databases — discover existing ones or add by "
            "name above.")
        self.lbl_tracked_empty.setProperty("class", "dim")
        tracked_layout.addWidget(self.lbl_tracked_empty)
        rowops = QHBoxLayout()
        rowops.setSpacing(8)
        self.btn_init = QPushButton("Init")
        self.btn_init.setToolTip("Initialize this database (-i base)")
        self.btn_init.clicked.connect(
            lambda: self._emit("init-db", self._selected_tracked()))
        rowops.addWidget(self.btn_init)
        self.btn_backup = style_button(QPushButton("Backup"), "save")
        self.btn_backup.clicked.connect(
            lambda: self._emit("backup-db", self._selected_tracked()))
        rowops.addWidget(self.btn_backup)
        self.btn_drop = style_button(QPushButton("Drop"), "drop")
        self.btn_drop.setProperty("role", "destructive")
        self.btn_drop.clicked.connect(
            lambda: self._emit("drop-db", self._selected_tracked()))
        rowops.addWidget(self.btn_drop)
        self.btn_untrack = QPushButton("Untrack")
        self.btn_untrack.clicked.connect(
            lambda: self._emit("untrack", self._selected_tracked()))
        rowops.addWidget(self.btn_untrack)
        rowops.addStretch(1)
        tracked_layout.addLayout(rowops)
        layout.addWidget(tracked_box, 1)

        ops_box = QGroupBox("Operations")
        ops_layout = QVBoxLayout(ops_box)
        ops_row = QHBoxLayout()
        ops_row.setSpacing(8)
        self.btn_discover = QPushButton("Discover databases")
        self.btn_discover.setToolTip("Find Postgres databases for this db user")
        self.btn_discover.clicked.connect(
            lambda: self._emit("discover", None))
        ops_row.addWidget(self.btn_discover)
        self.btn_refresh = QPushButton("Refresh states")
        self.btn_refresh.setToolTip("Re-query Postgres for every tracked DB")
        self.btn_refresh.clicked.connect(
            lambda: self._emit("refresh-states", None))
        ops_row.addWidget(self.btn_refresh)
        self.btn_restore = QPushButton("Restore…")
        self.btn_restore.setToolTip("Restore a pg_dump file into a database")
        self.btn_restore.clicked.connect(
            lambda: self._emit("restore", None))
        ops_row.addWidget(self.btn_restore)
        self.btn_validate = QPushButton("Validate config")
        self.btn_validate.setToolTip(
            "Compare odoo.conf db_* settings against live Postgres")
        self.btn_validate.clicked.connect(
            lambda: self._emit("validate", None))
        ops_row.addWidget(self.btn_validate)
        ops_row.addStretch(1)
        ops_layout.addLayout(ops_row)
        layout.addWidget(ops_box)

    # ------------------------------------------------------------------ API

    def show_instance(self, instance) -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
            tracked = instance.get("tracked_dbs", []) or []
            self._primary_db = instance.get("primary_db", "") or ""
        else:
            self._instance_id = instance.id
            tracked = list(instance.tracked_dbs or [])
            self._primary_db = instance.primary_db or ""
        self.refresh_databases(tracked)

    def refresh_databases(self, tracked: list) -> None:
        """Rebuild combo + tracked rows (states arrive via set_db_states)."""
        self.db_combo.clear()
        self.db_combo.addItems(tracked)
        try:
            self.db_combo.setCurrentIndex(tracked.index(self._primary_db))
        except ValueError:
            self.db_combo.setCurrentIndex(0 if tracked else -1)
        self.tracked_list.clear()
        for db_name in tracked:
            item = QTreeWidgetItem([db_name, "…", "…", "loading…"])
            item.setData(0, Qt.UserRole, db_name)
            if db_name == self._primary_db:
                item.setText(0, f"★ {db_name}")
            self.tracked_list.addTopLevelItem(item)
        self._last_states: dict = {}
        self._sync_empty()
        self._sync_row_buttons()

    def set_db_states(self, states: dict) -> None:
        """Fill live per-DB state (ground truth from db_state.py)."""
        from PySide6.QtGui import QColor

        self._last_states = states or {}
        for row in range(self.tracked_list.topLevelItemCount()):
            item = self.tracked_list.topLevelItem(row)
            db_name = item.data(0, Qt.UserRole)
            info = (states or {}).get(db_name) or {}
            name = f"★ {db_name}" if db_name == self._primary_db else db_name
            item.setText(0, name)
            item.setText(1, info.get("size", "—") or "—")
            item.setText(2,
                         f"v{info['odoo_version']}" if info.get("odoo_version")
                         else "—")
            if info.get("initialized"):
                item.setText(3, "initialized")
                item.setForeground(3, QColor("#2ec27e"))
            elif info.get("exists", True):
                item.setText(3, "exists, not initialized")
                item.setForeground(3, QColor("#e5a50a"))
            else:
                item.setText(3, "missing")
                item.setForeground(3, QColor("#e01b24"))
        for col in range(4):
            self.tracked_list.resizeColumnToContents(col)
        self._sync_row_buttons()

    # -------------------------------------------------------------- internals

    def _sync_empty(self) -> None:
        self.lbl_tracked_empty.setVisible(
            self.tracked_list.topLevelItemCount() == 0)

    def _selected_db(self):
        return self.db_combo.currentText() or None

    def _selected_tracked(self):
        item = self.tracked_list.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _on_track_manual(self) -> None:
        name = self.entry_manual_db.text().strip()
        if name:
            self._emit("track", name)
            self.entry_manual_db.clear()

    def _sync_row_buttons(self) -> None:
        if getattr(self, "_busy", False):
            for btn in (self.btn_set_primary, self.btn_switch,
                        self.btn_init, self.btn_backup, self.btn_drop,
                        self.btn_untrack, self.btn_discover, self.btn_refresh,
                        self.btn_restore, self.btn_validate):
                try:
                    btn.setEnabled(False)
                except Exception:
                    pass
            return
        db_name = self._selected_tracked()
        has = bool(db_name)
        states = getattr(self, "_last_states", {}) or {}
        initialized = bool((states.get(db_name) or {}).get("initialized"))
        # GTK parity: Init only for uninitialized DBs; primary never drops.
        self.btn_init.setEnabled(has and not initialized)
        self.btn_backup.setEnabled(has)
        self.btn_untrack.setEnabled(has)
        # B.4 parity: primary row has no Drop button at all.
        self.btn_drop.setEnabled(has and db_name != self._primary_db)

    def _emit(self, action: str, payload) -> None:
        if self._instance_id is not None:
            self.actionRequested.emit(action, self._instance_id, payload)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        self._busy = not enabled
        self._sync_row_buttons()
