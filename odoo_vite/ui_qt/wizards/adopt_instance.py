"""Adopt Instance wizard (PSQ-9.3): Locate → Gaps → Adopt.

Lenient conf validation ported exactly: present/missing reported per
field, never inventing values, gaps editable rather than hard-reject.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from odoo_vite.core import adopt, provisioning
from odoo_vite.core.registry import get_instance_by_name
from odoo_vite.ui_qt.widgets.wizard import Wizard, WorkerPage
from odoo_vite.ui_qt.workers import run_in_background


class LocatePage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Locate existing install")
        self.setSubTitle("Point at an odoo.conf and its community folder. "
                         "Nothing is written until Adopt.")
        layout = QVBoxLayout(self)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Instance name (must be unique):"))
        self.entry_name = QLineEdit()
        self.entry_name.setPlaceholderText("e.g. Legacy Prod Clone")
        name_row.addWidget(self.entry_name, 1)
        layout.addLayout(name_row)
        self.err_name = QLabel()
        self.err_name.setProperty("class", "error")
        layout.addWidget(self.err_name)
        self.entry_conf = QLineEdit()
        self.entry_conf.setPlaceholderText("path to odoo.conf")
        self.entry_conf.textChanged.connect(lambda _t: self._reparse())
        layout.addWidget(self._picker_row("odoo.conf:", self.entry_conf,
                                          self._browse_conf))
        self.entry_community = QLineEdit()
        self.entry_community.setPlaceholderText("path to community folder")
        self.entry_community.textChanged.connect(lambda _t: self._reparse())
        layout.addWidget(self._picker_row("Community folder:",
                                          self.entry_community,
                                          self._browse_community))
        self.lbl_detected = QLabel()
        self.lbl_detected.setProperty("class", "dim")
        self.lbl_detected.setWordWrap(True)
        layout.addWidget(self.lbl_detected)
        layout.addStretch(1)
        self._conf = ""
        self._community = ""
        self._parsed: dict = {}
        self._report: dict = {}

    def _picker_row(self, caption, entry, browse_cb):
        row = QHBoxLayout()
        row.addWidget(QLabel(caption))
        row.addWidget(entry, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(browse_cb)
        row.addWidget(btn)
        container = QWidget()
        container.setLayout(row)
        return container

    def _browse_conf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select odoo.conf", "", "Config files (odoo.conf *.conf)")
        if path:
            self.entry_conf.setText(path)

    def _browse_community(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select community folder")
        if folder:
            self.entry_community.setText(folder)

    def _reparse(self) -> None:
        from pathlib import Path

        self._conf = self.entry_conf.text().strip()
        self._community = self.entry_community.text().strip()
        self._parsed = adopt.parse_conf(self._conf) if self._conf else {}
        self._report = adopt.validate_adopted_conf(self._parsed)
        version = adopt.detect_version(self._community) \
            if self._community else ""
        bits = []
        if self._conf:
            missing = [f for f, s in self._report.items() if s == "missing"]
            bits.append("conf parsed" + (
                f" (missing: {', '.join(missing)})" if missing else " ✓"))
        if self._community:
            ok = (Path(self._community) / "odoo-bin").is_file()
            bits.append(f"odoo-bin {'found' if ok else 'NOT FOUND'}"
                        + (f", version {version}" if version else ""))
        self.lbl_detected.setText(" · ".join(bits))

    def validatePage(self) -> bool:  # noqa: N802
        from pathlib import Path

        name = self.entry_name.text().strip()
        if not name:
            return self._fail("Name is required.")
        if get_instance_by_name(name) is not None:
            return self._fail(f"An instance named '{name}' already exists.")
        self._reparse()
        if not self._conf or not Path(self._conf).is_file():
            return self._fail("Pick a readable odoo.conf first.")
        if not self._community or not (
                Path(self._community) / "odoo-bin").is_file():
            return self._fail("Community folder must contain odoo-bin.")
        self.err_name.setText("")
        return True

    def _fail(self, message: str) -> bool:
        self.err_name.setText(message)
        return False


class GapsPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Fill the gaps")
        self.setSubTitle("Missing fields are editable here — nothing is "
                         "invented, and Adopt never writes files.")
        layout = QVBoxLayout(self)
        self.gap_form = QFormLayout()
        layout.addLayout(self.gap_form, 1)
        db_row = QHBoxLayout()
        db_row.addWidget(QLabel("Primary database (required):"))
        self.entry_db = QLineEdit()
        db_row.addWidget(self.entry_db, 1)
        layout.addLayout(db_row)
        self.err = QLabel()
        self.err.setProperty("class", "error")
        layout.addWidget(self.err)
        self.gap_entries: dict[str, QLineEdit] = {}
    def initializePage(self) -> None:  # noqa: N802
        wizard = self.wizard()
        locate = wizard.locate_page
        parsed, report = locate._parsed, locate._report
        # Clear previous gap rows (keep nothing stale across Back/Next).
        while self.gap_form.count():
            child = self.gap_form.takeAt(0)
            if child.widget() is not None:
                child.widget().deleteLater()
        self.gap_entries = {}
        port = (parsed.get("xmlrpc_port") or parsed.get("http_port") or "")
        fields = [("Addons path", "addons_path",
                   parsed.get("addons_path", ""),
                   report.get("addons_path") == "missing"),
                  ("DB user", "db_user", parsed.get("db_user", ""),
                   report.get("db_user") == "missing"),
                  ("DB password", "db_password",
                   parsed.get("db_password", ""),
                   report.get("db_password") == "missing"),
                  ("Port", "port", port,
                   report.get("port") == "missing"),
                  ("Log file", "logfile", parsed.get("logfile", ""),
                   report.get("logfile") == "missing")]
        for caption, key, value, missing in fields:
            status = QLabel("MISSING — fill in:" if missing
                            else f"present: {value}"[:80])
            if missing:
                status.setProperty("class", "warning")
            else:
                status.setProperty("class", "dim")
            self.gap_form.addRow(caption + ":", status)
            if missing:
                entry = QLineEdit(value)
                self.gap_form.addRow("", entry)
                self.gap_entries[key] = entry
        if not self.entry_db.text().strip():
            name = locate.entry_name.text().strip()
            self.entry_db.setText(
                provisioning.slugify_db_name(name or "odoo"))

    def gap_overrides(self) -> dict:
        return {key: entry.text() for key, entry in self.gap_entries.items()}

    def validatePage(self) -> bool:  # noqa: N802
        if not self.entry_db.text().strip():
            self.err.setText("Primary database is required.")
            return False
        self.err.setText("")
        return True


class AdoptRunPage(WorkerPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Adopting")

    def initializePage(self) -> None:  # noqa: N802
        wizard = self.wizard()
        locate = wizard.locate_page
        gaps = wizard.gaps_page
        name = locate.entry_name.text().strip()
        overrides = {"primary_db": gaps.entry_db.text().strip()}
        overrides.update({k: v for k, v in gaps.gap_overrides().items()
                          if v.strip()})
        parts = adopt.split_addons(locate._parsed.get("addons_path", ""))
        if parts.get("enterprise"):
            overrides["enterprise_path"] = parts["enterprise"]
        self.status_label.setText(f"Adopting {name}…")
        self.progress.setVisible(True)

        def _done(ok: bool, message: str, data: dict) -> None:
            self.progress.setVisible(False)
            if ok:
                self.status_label.setText("Adopted ✓ (no files touched)")
                self.set_complete(True)
                wizard.instance_created((data or {}).get("id", ""))
            else:
                self.status_label.setText("Adopt failed")
                self.append_log(f"ERROR: {message}")

        run_in_background(
            self, adopt.adopt_instance, _done, name, locate._conf,
            locate._community, overrides=overrides)


class AdoptWizard(Wizard):
    """Adopt-existing-install wizard. Emits instanceCreated(instance_id)."""

    instanceCreated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Adopt Instance")
        self.locate_page = LocatePage(self)
        self.addPage(self.locate_page)
        self.gaps_page = GapsPage(self)
        self.addPage(self.gaps_page)
        self.addPage(AdoptRunPage(self))
        self.setMinimumSize(620, 500)

    def instance_created(self, instance_id: str) -> None:
        self.instanceCreated.emit(instance_id)
