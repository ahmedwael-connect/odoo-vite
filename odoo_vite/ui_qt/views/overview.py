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


_MISSING = object()  # sentinel: this payload doesn't carry the field


class OverviewPage(QWidget):
    actionRequested = Signal(str, str)  # (action, instance_id)

    ACTIONS = ("start", "stop", "restart", "remove")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._busy = False
        self._last_seen = None
        # Diff-before-repaint state: last rendered scalar per widget key,
        # plus the enterprise-badge inputs. Poll ticks with identical data
        # skip every write (no flicker, no FS scans).
        self._rendered: dict = {}
        self._ent_key: object = _MISSING  # _MISSING = must evaluate once
        self._ent_data: dict = {"state": "community"}
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

        self.venv_box = QHBoxLayout()
        self.venv_box.setSpacing(8)
        self.lbl_venv = QLabel()
        self.lbl_venv.setProperty("class", "warning")
        self.lbl_venv.setWordWrap(True)
        self.lbl_venv.setVisible(False)
        self.venv_box.addWidget(self.lbl_venv, 1)
        self.btn_venv = QPushButton("Rebuild venv now")
        self.btn_venv.setToolTip(
            "Fresh venv + Odoo requirements (minutes, needs network)")
        self.btn_venv.setVisible(False)
        self.btn_venv.clicked.connect(
            lambda: self._on_action("rebuild-venv"))
        self.venv_box.addWidget(self.btn_venv)
        layout.addLayout(self.venv_box)

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
        # New selection: drop all repaint caches so the page paints fully
        # once, then poll ticks diff against it.
        self._rendered = {}
        self._ent_key = _MISSING
        self.refresh_status(instance)

    def set_disk_text(self, text: str) -> None:
        self.disk_label.setText(text or "Disk: —")

    def _paint(self, key: str, value: str, setter) -> bool:
        """Diff-before-repaint: call setter(value) only on change."""
        if key in self._rendered and self._rendered[key] == value:
            return False
        self._rendered[key] = value
        if setter is not None:
            setter(value)
        return True

    def show_error(self, message: str) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    def refresh_status(self, instance) -> None:
        """Update status-dependent widgets (safe to call on poll ticks).

        Diff-before-repaint: poll payloads are partial dicts (status/port/
        version/cpu/…) while full Instance rows carry everything. A field
        only repaints when its key is present in this payload AND its value
        changed — so ticks never blank rows they don't know about and never
        rewrite identical text (no flicker).
        """
        self._last_seen = instance
        is_dict = isinstance(instance, dict)
        if is_dict:
            status = instance.get("status", "")
            version = instance.get("version", "")
            port = instance.get("port", "")
            path = instance.get("path", _MISSING)
            primary = instance.get("primary_db", _MISSING)
            db_user = instance.get("db_user", _MISSING)
            desc = instance.get("description", _MISSING)
            cpu = instance.get("cpu_percent", _MISSING)
            memory = instance.get("memory_mb", _MISSING)
            pw_storage = instance.get("password_storage", _MISSING)
        else:
            status = instance.status or ""
            version = instance.version or ""
            port = instance.port or ""
            path = instance.path or ""
            primary = instance.primary_db or ""
            db_user = instance.db_user or ""
            desc = instance.description or ""
            cpu = getattr(instance, "cpu_percent", _MISSING)
            memory = getattr(instance, "memory_mb", _MISSING)
            pw_storage = instance.password_storage or ""
        if desc is not _MISSING:
            self._paint("desc", str(desc), self.desc_label.setText)
            self.desc_label.setVisible(bool(desc))
        state = str(status).capitalize()
        if self._paint("state", state, None):
            self.status_label.setText(f"Status: {state}")
            self.status_label.setProperty(
                "class", "success" if state == "Running" else "dim")
            # Re-polish so the dynamic property restyles without rebuild.
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
        self._paint("version", str(version),
                    self._fields["version"].setText)
        self._paint("port", str(port), self._fields["port"].setText)
        if path is not _MISSING:
            self._paint("path", str(path), self._fields["path"].setText)
        if primary is not _MISSING:
            self._paint("primary_db", str(primary) or "—",
                        self._fields["primary_db"].setText)
        if db_user is not _MISSING:
            self._paint("db_user", str(db_user),
                        self._fields["db_user"].setText)
        if cpu is not _MISSING:
            self._paint(
                "cpu", f"{cpu:.0f}%" if isinstance(cpu, (int, float)) else "—",
                self._fields["cpu"].setText)
        if memory is not _MISSING:
            self._paint(
                "memory",
                f"{memory:.0f} MB" if isinstance(memory, (int, float)) else "—",
                self._fields["memory"].setText)
        running = state == "Running"
        if self._busy:
            # Busy gating wins over state gating (poll ticks must not
            # re-enable mid-operation).
            for btn in list(self._buttons.values()) + [
                    self.btn_secure, self.btn_browser, self.btn_clone,
                    self.btn_disk, self.btn_venv]:
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
        # Busy gating disables the box/buttons above; re-enable here so a
        # finished operation doesn't leave them stuck off.
        self.btn_secure.setEnabled(True)
        self.btn_venv.setEnabled(True)
        # H.2 parity: plaintext warning + one-click keyring sweep.
        # Poll dicts without the key keep the last state (never blank it).
        if pw_storage is not _MISSING:
            is_plain = (pw_storage or "") == "plaintext"
            self.lbl_security.setVisible(is_plain)
            self.btn_secure.setVisible(is_plain)
            if is_plain and self._paint("sec_text", "plain", None):
                self.lbl_security.setText(
                    "⚠ Database password stored in PLAINTEXT — click to "
                    "secure it in the OS keyring.")
        # ENT.1 parity: always-visible tri-state badge, cached. Filesystem
        # detection runs only for full rows whose enterprise inputs changed —
        # never on partial poll ticks (they carry no enterprise fields).
        if is_dict:
            ent_path = instance.get("enterprise_path", None)
            ent_id = instance.get("id", None)
        else:
            ent_path = getattr(instance, "enterprise_path", "")
            ent_id = getattr(instance, "id", None)
        if ent_path is None and is_dict and self._ent_key is not _MISSING:
            pass  # partial tick: keep cached badge
        else:
            ent_key = (ent_id, ent_path or "", version)
            if ent_key != self._ent_key:
                self._ent_key = ent_key
                try:
                    res = _enterprise.detect_enterprise(instance)
                    self._ent_data = res.data if res.ok else {}
                except Exception:
                    self._ent_data = {"state": "community"}
                self._paint_ent_badge(version)
        self._sync_venv_row(instance)

    def _paint_ent_badge(self, version: str) -> None:
        data = self._ent_data or {}
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

    def _sync_venv_row(self, instance) -> None:
        """U5.2: warn + offer rebuild when no usable venv python exists.

        Poll payloads (dicts) often lack venv fields — then the row keeps
        its last state instead of flickering.
        """
        from pathlib import Path as _Path

        if isinstance(instance, dict):
            venv_path = instance.get("venv_path", None)
            python_binary = instance.get("python_binary", None)
            name = instance.get("name", "instance")
        else:
            venv_path = getattr(instance, "venv_path", None)
            python_binary = getattr(instance, "python_binary", None)
            name = getattr(instance, "name", "instance")
        if venv_path is None and python_binary is None:
            return  # poll payload without venv info: keep last state
        override = (python_binary or "").strip()
        if override:
            effective = override
        elif (venv_path or "").strip():
            effective = f"{venv_path}/bin/python"
        else:
            effective = ""
        ok = bool(effective) and _Path(effective).is_file()
        self.lbl_venv.setVisible(not ok)
        self.btn_venv.setVisible(not ok)
        if not ok:
            where = effective or "(no venv configured)"
            self.lbl_venv.setText(
                f"⚠ No usable Python environment at {where} — rebuild "
                f"the venv before starting '{name}'.")

    def _on_action(self, action: str) -> None:
        if self._instance_id:
            self.actionRequested.emit(action, self._instance_id)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        self._busy = not enabled
        if self._last_seen is not None:
            self.refresh_status(self._last_seen)
