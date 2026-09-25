"""Databases page (PSQ-4.2): faithful port of the GTK Databases tab.

Tracked list with per-row state lines, switcher row, manual track row,
ops row (Refresh / Restore… / Validate), Discover button. Row actions
(Init / Backup / Drop / Untrack) act on the selected row — same set as
GTK's per-row buttons, adapted to a selection model. Emits
actionRequested(action, instance_id, payload); a DatabaseFlows controller
executes — this page never calls core/ directly.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DatabasesPage(QWidget):
    actionRequested = Signal(str, str, object)  # (action, instance_id, payload)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._primary_db: str = ""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        title = QLabel("Databases")
        title.setProperty("class", "heading")
        layout.addWidget(title)

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
        layout.addLayout(switch_row)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        manual_row.addWidget(QLabel("Database name:"))
        self.entry_manual_db = QLineEdit()
        self.entry_manual_db.setPlaceholderText("Add by name…")
        manual_row.addWidget(self.entry_manual_db, 1)
        btn_track = QPushButton("Track")
        btn_track.clicked.connect(self._on_track_manual)
        manual_row.addWidget(btn_track)
        layout.addLayout(manual_row)

        self.tracked_list = QListWidget()
        self.tracked_list.currentRowChanged.connect(
            lambda _row: self._sync_row_buttons())
        layout.addWidget(self.tracked_list, 1)

        rowops = QHBoxLayout()
        rowops.setSpacing(8)
        self.btn_init = QPushButton("Init")
        self.btn_init.setToolTip("Initialize this database (-i base)")
        self.btn_init.clicked.connect(
            lambda: self._emit("init-db", self._selected_tracked()))
        rowops.addWidget(self.btn_init)
        self.btn_backup = QPushButton("Backup")
        self.btn_backup.clicked.connect(
            lambda: self._emit("backup-db", self._selected_tracked()))
        rowops.addWidget(self.btn_backup)
        self.btn_drop = QPushButton("Drop")
        self.btn_drop.setProperty("role", "destructive")
        self.btn_drop.clicked.connect(
            lambda: self._emit("drop-db", self._selected_tracked()))
        rowops.addWidget(self.btn_drop)
        self.btn_untrack = QPushButton("Untrack")
        self.btn_untrack.clicked.connect(
            lambda: self._emit("untrack", self._selected_tracked()))
        rowops.addWidget(self.btn_untrack)
        rowops.addStretch(1)
        layout.addLayout(rowops)

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
        layout.addLayout(ops_row)

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
            marker = "  ★ primary" if db_name == self._primary_db else ""
            item = QListWidgetItem(f"{db_name}{marker}")
            item.setData(Qt.UserRole, db_name)
            self.tracked_list.addItem(item)
        self._last_states: dict = {}
        self._sync_row_buttons()

    def set_db_states(self, states: dict) -> None:
        """Fill live per-DB state lines (ground truth from db_state.py)."""
        self._last_states = states or {}
        for row in range(self.tracked_list.count()):
            item = self.tracked_list.item(row)
            db_name = item.data(Qt.UserRole)
            info = (states or {}).get(db_name) or {}
            parts = []
            if info.get("size"):
                parts.append(info["size"])
            if info.get("odoo_version"):
                parts.append(f"v{info['odoo_version']}")
            if info.get("initialized"):
                parts.append("initialized")
            elif info.get("exists", True):
                parts.append("exists, not initialized")
            else:
                parts.append("missing")
            suffix = f"  ·  {' · '.join(parts)}" if parts else ""
            base = f"{db_name}{'  ★ primary' if db_name == self._primary_db else ''}"
            item.setText(base + suffix)
        self._sync_row_buttons()

    # -------------------------------------------------------------- internals

    def _selected_db(self):
        return self.db_combo.currentText() or None

    def _selected_tracked(self):
        item = self.tracked_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_track_manual(self) -> None:
        name = self.entry_manual_db.text().strip()
        if name:
            self._emit("track", name)
            self.entry_manual_db.clear()

    def _sync_row_buttons(self) -> None:
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
