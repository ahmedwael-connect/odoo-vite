"""Configuration page (PSQ-6.2): faithful port of the GTK Configuration tab.

Conf table (read-only display), common-key editors, managed addons_path
row + manager link, raw key/value editor, save/restore, regenerate,
metadata section. Display reads (refresh_conf) run locally and instantly,
like GTK; writes emit actions for the flows controller.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402

from odoo_vite.core import conf_manager  # noqa: E402
from odoo_vite.core.registry import get_instance  # noqa: E402

COMMON_KEYS = ["db_host", "db_port", "db_user", "xmlrpc_port", "logfile"]
LOG_LEVELS = ["info", "debug", "debug_sql", "warning", "error", "critical"]


def elided_middle(text: str, limit: int = 60) -> str:
    """A.1 rule, Qt form: GTK had set_ellipsize(MIDDLE)+max-width; QLabel
    has neither, so truncate in Python. Always pair with setToolTip(full).
    An unbroken long string otherwise forces the tab's min-width wide."""
    text = text or ""
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    return text[:keep] + "…" + text[-(limit - 1 - keep):]


class ConfigurationPage(QWidget):
    actionRequested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._conf_options: dict = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        title = QLabel("Configuration (odoo.conf)")
        title.setProperty("class", "heading")
        layout.addWidget(title)
        self.lbl_conf_path = QLabel()
        self.lbl_conf_path.setProperty("class", "dim")
        layout.addWidget(self.lbl_conf_path)

        self.conf_table = QListWidget()
        self.conf_table.setMaximumHeight(220)
        layout.addWidget(self.conf_table)

        form_title = QLabel("Edit common keys")
        form_title.setProperty("class", "heading")
        layout.addWidget(form_title)
        self.conf_entries: dict[str, QLineEdit] = {}
        for key in COMMON_KEYS:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(key)
            lbl.setFixedWidth(110)
            row.addWidget(lbl)
            entry = QLineEdit()
            row.addWidget(entry, 1)
            layout.addLayout(row)
            self.conf_entries[key] = entry
        # addons_path is managed, not typed: read-only + manager link.
        addons_row = QHBoxLayout()
        addons_row.setSpacing(8)
        lbl = QLabel("addons_path")
        lbl.setFixedWidth(110)
        addons_row.addWidget(lbl)
        self.lbl_addons_ro = QLabel()
        self.lbl_addons_ro.setProperty("class", "dim")
        self.lbl_addons_ro.setWordWrap(False)
        addons_row.addWidget(self.lbl_addons_ro, 1)
        self.btn_addons = QPushButton("Manage…")
        self.btn_addons.clicked.connect(
            lambda: self._emit("addons-manage", None))
        addons_row.addWidget(self.btn_addons)
        layout.addLayout(addons_row)

        raw_row = QHBoxLayout()
        raw_row.setSpacing(8)
        self.entry_raw_key = QLineEdit()
        self.entry_raw_key.setPlaceholderText("raw key")
        raw_row.addWidget(self.entry_raw_key, 1)
        self.entry_raw_value = QLineEdit()
        self.entry_raw_value.setPlaceholderText(
            "value (empty deletes the key)")
        raw_row.addWidget(self.entry_raw_value, 1)
        btn_raw = QPushButton("Set")
        btn_raw.clicked.connect(self._on_raw_set)
        raw_row.addWidget(btn_raw)
        layout.addLayout(raw_row)

        self.lbl_conf_notice = QLabel()
        self.lbl_conf_notice.setProperty("class", "warning")
        layout.addWidget(self.lbl_conf_notice)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self.btn_conf_save = style_button(
            QPushButton("Save changes"), "save", primary=True)
        self.btn_conf_save.clicked.connect(self._on_conf_save)
        save_row.addWidget(self.btn_conf_save)
        self.btn_conf_restore = QPushButton("Restore last backup")
        self.btn_conf_restore.clicked.connect(
            lambda: self._emit("conf-restore", None))
        save_row.addWidget(self.btn_conf_restore)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        adv_title = QLabel("Advanced")
        adv_title.setProperty("class", "heading")
        layout.addWidget(adv_title)
        self.btn_conf_regen = QPushButton("Regenerate from registry…")
        self.btn_conf_regen.setToolTip(
            "Rebuild [options] from registry fields (overwrites manual edits)")
        self.btn_conf_regen.clicked.connect(
            lambda: self._emit("conf-regenerate", None))
        layout.addWidget(self.btn_conf_regen)

        meta_title = QLabel("Metadata")
        meta_title.setProperty("class", "heading")
        layout.addWidget(meta_title)
        layout.addWidget(QLabel("Description (registry only, for organization)"))
        self.entry_description = QLineEdit()
        layout.addWidget(self.entry_description)
        workers_row = QHBoxLayout()
        workers_row.setSpacing(8)
        workers_row.addWidget(QLabel("Workers"))
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(0, 64)
        workers_row.addWidget(self.spin_workers)
        workers_row.addWidget(QLabel("Log level"))
        self.drop_log_level = QComboBox()
        self.drop_log_level.addItems(LOG_LEVELS)
        workers_row.addWidget(self.drop_log_level)
        workers_row.addStretch(1)
        layout.addLayout(workers_row)
        hint = QLabel(
            "Workers: 0 = single-process dev mode (the default so far). "
            "Above 0 needs a free gevent/longpolling port and more RAM; "
            "cron moves to a dedicated worker. When in doubt, keep 0.")
        hint.setWordWrap(True)
        hint.setProperty("class", "dim")
        layout.addWidget(hint)
        py_row = QHBoxLayout()
        py_row.setSpacing(8)
        py_row.addWidget(QLabel("Custom interpreter"))
        self.entry_python = QLineEdit()
        self.entry_python.setPlaceholderText("empty = venv's own python")
        py_row.addWidget(self.entry_python, 1)
        btn_py_browse = QPushButton("Browse…")
        btn_py_browse.clicked.connect(self._on_browse_python)
        py_row.addWidget(btn_py_browse)
        layout.addLayout(py_row)
        self.err_python = QLabel()
        self.err_python.setProperty("class", "error")
        layout.addWidget(self.err_python)
        btn_meta_save = style_button(
            QPushButton("Save metadata"), "save", primary=True)
        btn_meta_save.clicked.connect(self._on_meta_save)
        layout.addWidget(btn_meta_save)
        layout.addStretch(1)

    # ------------------------------------------------------------------ API

    def show_instance(self, instance) -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
        else:
            self._instance_id = instance.id
        self.refresh_conf()

    def refresh_conf(self) -> None:
        """Reload the conf table + editors from disk (local, instant)."""
        self.conf_table.clear()
        inst = (get_instance(self._instance_id)
                if self._instance_id else None)
        if inst is None or not inst.conf_path:
            self.lbl_conf_path.setText("No conf recorded.")
            self._conf_options = {}
            return
        self.lbl_conf_path.setText(inst.conf_path)
        self.lbl_conf_path.setToolTip(inst.conf_path)
        res = conf_manager.read_conf(inst.conf_path)
        if not res.ok:
            self.conf_table.addItem(f"Cannot read conf: {res.message}")
            self._conf_options = {}
            return
        self._conf_options = dict(res.data["options"])
        for key, value in res.data["options"].items():
            # Long values show elided; full text in tooltip (A.1 rule).
            shown = elided_middle(value)
            item = QListWidgetItem(f"{key} = {shown}")
            item.setToolTip(f"{key} = {value}")
            self.conf_table.addItem(item)
        for key, entry in self.conf_entries.items():
            entry.setText(self._conf_options.get(key, ""))
        addons = self._conf_options.get("addons_path", "—")
        self.lbl_addons_ro.setText(elided_middle(addons))
        self.lbl_addons_ro.setToolTip(addons)
        info = conf_manager.conf_backup_info(inst.conf_path)
        self.btn_conf_restore.setEnabled(info is not None)
        self.btn_conf_restore.setToolTip(
            f"Restore from {info['path']}" if info else "No backup yet")
        self.entry_description.setText(inst.description or "")
        self.spin_workers.setValue(inst.workers or 0)
        try:
            self.drop_log_level.setCurrentIndex(
                LOG_LEVELS.index(inst.log_level or "info"))
        except ValueError:
            self.drop_log_level.setCurrentIndex(0)
        self.entry_python.setText(inst.python_binary or "")

    # -------------------------------------------------------------- internals

    def _on_conf_save(self) -> None:
        changes = {}
        for key, entry in self.conf_entries.items():
            new = entry.text()
            old = self._conf_options.get(key, "")
            if new != old:
                changes[key] = new
        if not changes:
            self.lbl_conf_notice.setText("No changes to save.")
            return
        self.lbl_conf_notice.setText("")
        self._emit("conf-save", changes)

    def _on_raw_set(self) -> None:
        key = self.entry_raw_key.text().strip()
        if not key:
            self.lbl_conf_notice.setText("Enter a key name first.")
            return
        value = self.entry_raw_value.text()
        self.entry_raw_key.clear()
        self.entry_raw_value.clear()
        self._emit("conf-save", {key: (None if value == "" else value)})

    def _on_browse_python(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Python interpreter", "", "All files (*)")
        if path:
            self.entry_python.setText(path)

    def _on_meta_save(self) -> None:
        import os

        pybin = self.entry_python.text().strip()
        if pybin and not (os.path.isfile(pybin)
                          and os.access(pybin, os.X_OK)):
            self.err_python.setText(f"Not an executable: {pybin}")
            return
        self.err_python.setText("")
        self._emit("meta-save", {
            "description": self.entry_description.text(),
            "workers": int(self.spin_workers.value()),
            "log_level": self.drop_log_level.currentText() or "info",
            "python_binary": pybin,
        })

    def _emit(self, action: str, payload) -> None:
        if self._instance_id is not None:
            self.actionRequested.emit(action, self._instance_id, payload)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        for btn in (self.btn_addons, self.btn_conf_save,
                    self.btn_conf_restore, self.btn_conf_regen):
            try:
                btn.setEnabled(enabled)
            except Exception:
                pass
