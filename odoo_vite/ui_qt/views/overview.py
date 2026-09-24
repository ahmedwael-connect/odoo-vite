"""Overview page (PSQ-3.3): instance identity, stats, lifecycle actions.

Mirrors the GTK Overview tab's information (name, status, version, port,
paths, primary DB) with Start/Stop/Restart/Remove buttons. Buttons emit
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


class OverviewPage(QWidget):
    actionRequested = Signal(str, str)  # (action, instance_id)

    ACTIONS = ("start", "stop", "restart", "remove")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        self.name_label = QLabel()
        self.name_label.setProperty("class", "heading")
        layout.addWidget(self.name_label)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        form = QFormLayout()
        form.setSpacing(4)
        self._fields: dict[str, QLabel] = {}
        for key, caption in (("version", "Version"), ("port", "Port"),
                             ("path", "Path"), ("primary_db", "Primary DB"),
                             ("db_user", "DB user")):
            value = QLabel()
            value.setProperty("class", "dim")
            value.setWordWrap(True)
            form.addRow(caption + ":", value)
            self._fields[key] = value
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}
        for action in self.ACTIONS:
            btn = QPushButton(action.capitalize())
            if action == "remove":
                btn.setProperty("role", "destructive")
            btn.clicked.connect(
                lambda _checked=False, a=action: self._on_action(a))
            btn_row.addWidget(btn)
            self._buttons[action] = btn
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
        self.refresh_status(instance)

    def refresh_status(self, instance) -> None:
        """Update status-dependent widgets (safe to call on poll ticks)."""
        if isinstance(instance, dict):
            status = instance.get("status", "")
            version = instance.get("version", "")
            port = instance.get("port", "")
            path = instance.get("path", "")
            primary = instance.get("primary_db", "")
            db_user = instance.get("db_user", "")
        else:
            status = instance.status or ""
            version = instance.version or ""
            port = instance.port or ""
            path = instance.path or ""
            primary = instance.primary_db or ""
            db_user = instance.db_user or ""
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
        running = state == "Running"
        self._buttons["start"].setEnabled(not running)
        self._buttons["stop"].setEnabled(running)
        self._buttons["restart"].setEnabled(running)

    def _on_action(self, action: str) -> None:
        if self._instance_id:
            self.actionRequested.emit(action, self._instance_id)
