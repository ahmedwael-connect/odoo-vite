"""Overview page (PSQ-3.3): instance identity, stats, lifecycle actions.

Shows instance identity (name, status, version, port, paths, primary DB)
with Start/Stop/Restart/Remove/Clone buttons. Buttons emit
actionRequested(action, instance_id); a LifecycleFlows controller (flows/)
executes them — this page never calls core/ directly.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402

from odoo_vite.core import enterprise as _enterprise  # noqa: E402


class OverviewPage(QWidget):
    actionRequested = Signal(str, str)  # (action, instance_id)

    ACTIONS = ("start", "stop", "restart", "remove")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._busy = False
        self._last_seen = None
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        self.name_label = QLabel()
        self.name_label.setProperty("class", "heading")
        layout.addWidget(self.name_label)

        self.desc_label = QLabel()
        self.desc_label.setProperty("class", "dim")
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        self.error_label = QLabel()
        self.error_label.setProperty("class", "error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.ent_label = QLabel()
        self.ent_label.setWordWrap(True)
        layout.addWidget(self.ent_label)

        disk_row = QHBoxLayout()
        disk_row.setSpacing(8)
        self.disk_label = QLabel("Disk: —")
        self.disk_label.setProperty("class", "dim")
        disk_row.addWidget(self.disk_label, 1)
        self.btn_disk = QPushButton("Measure disk")
        self.btn_disk.setToolTip(
            "Measure this instance's folder size (runs in background)")
        self.btn_disk.clicked.connect(
            lambda: self._on_action("measure-disk"))
        disk_row.addWidget(self.btn_disk)
        layout.addLayout(disk_row)

        form = QFormLayout()
        form.setSpacing(4)
        self._fields: dict[str, QLabel] = {}
        for key, caption in (("version", "Version"), ("port", "Port"),
                              ("path", "Path"), ("primary_db", "Primary DB"),
                              ("db_user", "DB user"), ("cpu", "CPU"),
                              ("memory", "Memory")):
            value = QLabel()
            value.setProperty("class", "dim")
            value.setWordWrap(True)
            form.addRow(caption + ":", value)
            self._fields[key] = value
        layout.addLayout(form)

        self.security_box = QHBoxLayout()
        self.security_box.setSpacing(8)
        self.lbl_security = QLabel()
        self.lbl_security.setProperty("class", "warning")
        self.lbl_security.setWordWrap(True)
        self.lbl_security.setVisible(False)
        self.security_box.addWidget(self.lbl_security, 1)
        self.btn_secure = QPushButton("Move to keyring now")
        self.btn_secure.setVisible(False)
        self.btn_secure.clicked.connect(
            lambda: self._on_action("secure"))
        self.security_box.addWidget(self.btn_secure)
        layout.addLayout(self.security_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}
        for action in self.ACTIONS:
            btn = QPushButton(action.capitalize())
            if action in ("start", "stop", "restart", "remove"):
                style_button(btn, action)
            if action == "remove":
                btn.setProperty("role", "destructive")
            btn.clicked.connect(
                lambda _checked=False, a=action: self._on_action(a))
            btn_row.addWidget(btn)
            self._buttons[action] = btn
        self.btn_browser = QPushButton("Open in browser")
        self.btn_browser.clicked.connect(
            lambda: self._on_action("browser"))
        btn_row.addWidget(self.btn_browser)
        self.btn_clone = QPushButton("Clone")
        self.btn_clone.setToolTip(
            "Duplicate files + settings (no databases; venv rebuilt later)")
        self.btn_clone.clicked.connect(
            lambda: self._on_action("clone"))
        btn_row.addWidget(self.btn_clone)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def show_instance(self, instance) -> None:
        """instance: core Instance row (or dict with the same keys)."""
        if isinstance(instance, dict):
            get = instance.get
            self._instance_id = instance.get("id")
        else:
            get = getattr
            self._instance_id = instance.id
        name = get("name", "?") if isinstance(instance, dict) else instance.name
        self.name_label.setText(str(name))
        self.show_error("")
        self.disk_label.setText("Disk: —")
        self.refresh_status(instance)

    def set_disk_text(self, text: str) -> None:
        self.disk_label.setText(text or "Disk: —")

    def show_error(self, message: str) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def refresh_status(self, instance) -> None:
        """Update status-dependent widgets (safe to call on poll ticks)."""
        self._last_seen = instance
        if isinstance(instance, dict):
            status = instance.get("status", "")
            version = instance.get("version", "")
            port = instance.get("port", "")
            path = instance.get("path", "")
            primary = instance.get("primary_db", "")
            db_user = instance.get("db_user", "")
            desc = instance.get("description", "")
            cpu = instance.get("cpu_percent", "")
            memory = instance.get("memory_mb", "")
            pw_storage = instance.get("password_storage", "")
        else:
            status = instance.status or ""
            version = instance.version or ""
            port = instance.port or ""
            path = instance.path or ""
            primary = instance.primary_db or ""
            db_user = instance.db_user or ""
            desc = instance.description or ""
            cpu = getattr(instance, "cpu_percent", "")
            memory = getattr(instance, "memory_mb", "")
            pw_storage = instance.password_storage or ""
        self.desc_label.setText(str(desc))
        self.desc_label.setVisible(bool(desc))
        state = str(status).capitalize()
        self.status_label.setText(f"Status: {state}")
        self.status_label.setProperty(
            "class", "success" if state == "Running" else "dim")
        # Re-polish so the dynamic property restyles without rebuild.
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._fields["version"].setText(str(version))
        self._fields["port"].setText(str(port))
        self._fields["path"].setText(str(path))
        self._fields["primary_db"].setText(str(primary) or "—")
        self._fields["db_user"].setText(str(db_user))
        self._fields["cpu"].setText(
            f"{cpu:.0f}%" if isinstance(cpu, (int, float)) else "—")
        self._fields["memory"].setText(
            f"{memory:.0f} MB" if isinstance(memory, (int, float)) else "—")
        running = state == "Running"
        if self._busy:
            # Busy gating wins over state gating (poll ticks must not
            # re-enable mid-operation).
            for btn in list(self._buttons.values()) + [
                    self.btn_secure, self.btn_browser, self.btn_clone,
                    self.btn_disk]:
                try:
                    btn.setEnabled(False)
                except Exception:
                    pass
            return
        self._buttons["start"].setEnabled(not running)
        self._buttons["stop"].setEnabled(running)
        self._buttons["restart"].setEnabled(running)
        self.btn_browser.setEnabled(running)
        self.btn_clone.setEnabled(not running)
        # H.2 parity: plaintext warning + one-click keyring sweep.
        is_plain = (pw_storage or "") == "plaintext"
        self.lbl_security.setVisible(is_plain)
        self.btn_secure.setVisible(is_plain)
        if is_plain:
            self.lbl_security.setText(
                "⚠ Database password stored in PLAINTEXT — click to secure "
                "it in the OS keyring.")
        # ENT.1 parity: always-visible tri-state badge.
        try:
            res = _enterprise.detect_enterprise(instance)
            data = res.data if res.ok else {}
        except Exception:
            data = {"state": "community"}
        self.ent_label.setProperty("class", "")
        state_name = data.get("state", "community")
        major = data.get("enterprise_major", "")
        if state_name == "valid" and data.get("match") is True:
            self.ent_label.setText(
                f"Enterprise addons {major} ✓ match Odoo {version}.")
        elif state_name == "valid" and data.get("match") is False:
            self.ent_label.setText(
                f"⚠ Enterprise addons {major} do NOT match Odoo "
                f"{version} — verify compatibility.")
            self.ent_label.setProperty("class", "warning")
        elif state_name == "invalid":
            self.ent_label.setText(
                "⚠ Enterprise path is set but not recognizable.")
            self.ent_label.setProperty("class", "warning")
        elif state_name == "valid":
            self.ent_label.setText(
                "Enterprise addons set, version unknown — verify "
                "compatibility.")
        else:
            self.ent_label.setText("Community edition.")
            self.ent_label.setProperty("class", "dim")

    def _on_action(self, action: str) -> None:
        if self._instance_id:
            self.actionRequested.emit(action, self._instance_id)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        self._busy = not enabled
        if self._last_seen is not None:
            self.refresh_status(self._last_seen)
