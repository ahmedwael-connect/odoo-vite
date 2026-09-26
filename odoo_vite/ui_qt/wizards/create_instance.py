"""Create Instance wizard (PSQ-9.2): the pattern-defining wizard.

Version (async branch list) → System check (advisory report) → Details
(validated form) → Provision (streaming worker + Cancel + Retry/Discard).
Draft safety comes from core/provisioning (registry row first, draft
status, idempotent per-step skips) — the UI only surfaces Retry (resume)
and Discard (draft removal). Emits instanceCreated(instance_id).
"""

import secrets
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from odoo_vite.core import git_manager, provisioning, system_check
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import get_instance_by_name
from odoo_vite.ui_qt.widgets.selection_list import SelectionList
from odoo_vite.ui_qt.widgets.wizard import Wizard, WorkerPage
from odoo_vite.ui_qt.workers import run_in_background


class VersionPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Odoo version")
        self.setSubTitle("Pick the version branch to clone.")
        layout = QVBoxLayout(self)
        self.branch_list = SelectionList(multi=False, parent=self)
        layout.addWidget(self.branch_list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(lambda: self.load_branches())
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)
        self.status = QLabel("Loading branches…")
        self.status.setProperty("class", "dim")
        layout.addWidget(self.status)

    def initializePage(self) -> None:  # noqa: N802
        self.load_branches()

    def load_branches(self, search: str = "") -> None:
        self.status.setText("Loading branches…")

        def _done(ok: bool, message: str, data: dict) -> None:
            # list_odoo_branches returns data as a bare LIST (GTK parity),
            # not {"branches": [...]} — accept both, never leave the list
            # empty while the count reads fine (A.2).
            if isinstance(data, dict):
                branches = data.get("branches", [])
            else:
                branches = data or []
            self.branch_list.set_items([
                {"id": b, "title": b} for b in branches])
            self.status.setText(
                message if ok else f"Branch list failed: {message}")

        run_in_background(self, _branches_payload, _done, search)

    def validatePage(self) -> bool:  # noqa: N802
        if not self.branch_list.selected_id():
            self.status.setText("Pick a version branch first.")
            return False
        return True

    def selected_version(self):
        return self.branch_list.selected_id()


def _branches_payload(search: str):
    from odoo_vite.core.result import Result

    res = git_manager.list_odoo_branches(search or "")
    if not res.ok:
        return Result.failure(res.message)
    # Normalize: core returns data as a bare list; the delivery signal
    # only carries dicts, so wrap here (thin adapter at the boundary).
    branches = res.data if isinstance(res.data, list) else []
    return Result(ok=True, message=res.message,
                  data={"branches": branches})


class SysCheckPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("System check")
        self.setSubTitle("Prerequisites for the selected version. "
                         "Advisory — you can continue with warnings.")
        layout = QVBoxLayout(self)
        self.report_list = QListWidget()
        layout.addWidget(self.report_list, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    def initializePage(self) -> None:  # noqa: N802
        wizard = self.wizard()
        version = wizard.version_page.selected_version() if wizard else ""
        self.report_list.clear()
        self.summary.setText(f"Checking requirements for Odoo {version}…")

        def _done(ok: bool, message: str, data: dict) -> None:
            checks = data.get("checks", []) if ok else []
            for check in checks:
                mark = ("✅" if check.get("ok") else "⚠")
                self.report_list.addItem(
                    f"{mark} {check.get('name', '?')}: "
                    f"{check.get('detail', '')}")
            self.summary.setText(message)

        run_in_background(
            self, _syscheck_payload, _done,
            version if isinstance(version, str) else "")

    def validatePage(self) -> bool:  # noqa: N802
        return True


def _syscheck_payload(version: str):
    return system_check.check_requirements(version or "")


class DetailsPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Instance details")
        form = QFormLayout(self)
        self.entry_name = QLineEdit()
        self.entry_name.setPlaceholderText("e.g. Client A — Odoo 17")
        form.addRow("Instance name (must be unique):", self.entry_name)
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.spin_port.setValue(8069)
        form.addRow("Port:", self.spin_port)
        self.entry_dbuser = QLineEdit("odoo")
        form.addRow("Database user:", self.entry_dbuser)
        pw_row = QHBoxLayout()
        self.entry_dbpass = QLineEdit("odoo")
        self.entry_dbpass.setEchoMode(QLineEdit.Password)
        pw_row.addWidget(self.entry_dbpass, 1)
        btn_gen = QPushButton("Generate")
        btn_gen.setToolTip("Generate a strong random password")
        btn_gen.clicked.connect(self._generate_password)
        pw_row.addWidget(btn_gen)
        form.addRow("Database password:", pw_row)
        self.lbl_keyring = QLabel(
            "Stored in the OS keyring; check below only to opt out explicitly.")
        self.lbl_keyring.setProperty("class", "dim")
        self.lbl_keyring.setWordWrap(True)
        form.addRow(self.lbl_keyring)
        self.check_plaintext = QCheckBox(
            "I understand the risk, store this password in plaintext locally")
        form.addRow(self.check_plaintext)
        self.entry_dbname = QLineEdit()
        self.entry_dbname.setPlaceholderText("odoo database name")
        form.addRow("Database name:", self.entry_dbname)
        self.err = QLabel()
        self.err.setProperty("class", "error")
        self.err.setWordWrap(True)
        form.addRow(self.err)

    def _generate_password(self) -> None:
        alphabet = ("abcdefghjkmnpqrstuvwxyz"
                    "ABCDEFGHJKMNPQRSTUVWXYZ23456789")
        self.entry_dbpass.setText(
            "".join(secrets.choice(alphabet) for _ in range(20)))

    def values(self) -> dict:
        return {"name": self.entry_name.text().strip(),
                "port": int(self.spin_port.value()),
                "db_user": self.entry_dbuser.text().strip() or "odoo",
                "db_password": self.entry_dbpass.text(),
                "plaintext": self.check_plaintext.isChecked(),
                "db_name": self.entry_dbname.text().strip()}

    def validatePage(self) -> bool:  # noqa: N802
        from odoo_vite.core.db_manager import is_valid_identifier

        values = self.values()
        if not values["name"]:
            return self._fail("Instance name is required.")
        if get_instance_by_name(values["name"]) is not None:
            return self._fail(
                f"An instance named '{values['name']}' already exists.")
        if not provisioning.is_port_free(values["port"]):
            return self._fail(
                f"Port {values['port']} is already in use — pick a free one.")
        if not values["db_user"]:
            return self._fail("Database user is required.")
        if not values["db_password"] and not values["plaintext"]:
            return self._fail("Set a password or explicitly opt out.")
        if not values["db_name"] or not is_valid_identifier(values["db_name"]):
            return self._fail("Database name must be a valid identifier.")
        self.err.setText("")
        return True

    def _fail(self, message: str) -> bool:
        self.err.setText(message)
        return False


class ProvisionPage(WorkerPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Provisioning")
        self.setSubTitle("Cloning, venv, requirements, conf — resumable.")
        self._instance = None
        self._cancel_event: threading.Event | None = None
        self._provisioning = False
        row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        row.addWidget(self.btn_cancel)
        self.btn_retry = QPushButton("Retry (resumes)")
        self.btn_retry.clicked.connect(lambda: self.start_work())
        self.btn_retry.setVisible(False)
        row.addWidget(self.btn_retry)
        self.btn_discard = QPushButton("Discard draft")
        self.btn_discard.setProperty("role", "destructive")
        self.btn_discard.clicked.connect(self._on_discard)
        self.btn_discard.setVisible(False)
        row.addWidget(self.btn_discard)
        row.addStretch(1)
        self.layout().addLayout(row)

    def initializePage(self) -> None:  # noqa: N802
        self.start_work()

    def _build_instance(self) -> Instance:
        wizard = self.wizard()
        details = wizard.details_page.values()
        version = wizard.version_page.selected_version() or ""
        if self._instance is None:
            path = provisioning.unique_instance_path(
                details["name"] or "odoo")
            self._instance = Instance(
                name=details["name"], version=version, mode="managed",
                path=str(path), venv_path=str(path / "venv"),
                community_path=str(path / "community"),
                enterprise_path="",
                custom_addons_path=str(path / "custom_addons"),
                conf_path=str(path / "odoo.conf"),
                log_path=str(path / "logs" / "odoo.log"),
                port=int(details["port"]),
                db_user=details["db_user"] or "odoo",
                db_password=details["db_password"],
                password_storage="plaintext",  # resolved at register
                primary_db=details["db_name"],
                tracked_dbs=[details["db_name"]] if details["db_name"]
                else [],
                status="draft")
        else:  # Retry keeps id + path stable, refreshes mutable fields.
            inst = self._instance
            inst.version = version or inst.version
            inst.port = int(details["port"])
            inst.db_user = details["db_user"] or "odoo"
            inst.db_password = details["db_password"]
            inst.primary_db = details["db_name"]
            inst.tracked_dbs = [details["db_name"]] if details["db_name"] \
                else []
        return self._instance

    def start_work(self) -> None:
        if self._provisioning:
            return
        self._provisioning = True
        self.set_complete(False)
        self.btn_retry.setVisible(False)
        self.btn_discard.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.status_label.setText("Provisioning…")
        self.progress.setVisible(True)
        self._cancel_event = threading.Event()
        inst = self._build_instance()
        wizard = self.wizard()
        details = wizard.details_page.values()

        def _done(ok: bool, message: str, data: dict) -> None:
            self._provisioning = False
            self.progress.setVisible(False)
            self.btn_cancel.setVisible(False)
            if ok:
                self.status_label.setText("Instance ready ✓")
                self.set_complete(True)
                wizard.instance_created(
                    (data or {}).get("instance_id", inst.id))
            else:
                failed = (data or {}).get("failed_step", "?")
                self.status_label.setText(f"Failed at step: {failed}")
                self.append_log(f"ERROR: {message}")
                self.btn_retry.setVisible(True)
                self.btn_discard.setVisible(True)

        run_in_background(
            self, provisioning.provision_instance, _done, inst,
            progress_cb=self.logAppended.emit,
            cancel=(self._cancel_event.is_set
                    if self._cancel_event is not None else None),
            allow_plaintext=bool(details.get("plaintext", False)))

    def _on_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.btn_cancel.setEnabled(False)
        self.status_label.setText("Cancelling… (waiting on subprocess)")

    def _on_discard(self) -> None:
        if self._instance is None:
            self.wizard().reject()
            return
        self.status_label.setText("Discarding draft…")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self._instance = None
            self.wizard().reject()

        run_in_background(
            self, provisioning.discard_draft, _done, self._instance.id)


class CreateWizard(Wizard):
    """New-instance wizard. Emits instanceCreated(instance_id)."""

    instanceCreated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Instance")
        self.version_page = VersionPage(self)
        self.addPage(self.version_page)
        self.addPage(SysCheckPage(self))
        self.details_page = DetailsPage(self)
        self.addPage(self.details_page)
        self.addPage(ProvisionPage(self))
        self.setMinimumSize(640, 520)

    def instance_created(self, instance_id: str) -> None:
        self.instanceCreated.emit(instance_id)
