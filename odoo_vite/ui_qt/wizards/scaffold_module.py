"""Scaffold Module wizard (PSQ-9.3): Definition → Generate + Install.

Acceptance rule (PM): a generated module isn't done until it installs
cleanly through the real Module Install flow — so this wizard ends by
installing into a chosen database on a chosen instance and reporting
the install result, not just "folder looks right".
"""

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
    QWizardPage,
)

from odoo_vite.core import module_scaffolder
from odoo_vite.core.db_state import get_db_state
from odoo_vite.core.registry import (
    get_db_password,
    get_instance,
    list_instances,
)
from odoo_vite.ui_qt.widgets.wizard import Wizard, WorkerPage
from odoo_vite.ui_qt.workers import run_in_background

FIELD_TYPES = ["char", "text", "integer", "float", "boolean", "date",
               "datetime", "html"]


class DefinitionPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("New module")
        self.setSubTitle("Generates into a custom_addons folder, then "
                         "installs into a database as acceptance.")
        form = QFormLayout(self)
        self.entry_tech = QLineEdit()
        self.entry_tech.setPlaceholderText("my_library (a-z, _)")
        form.addRow("Technical name:", self.entry_tech)
        self.entry_pretty = QLineEdit()
        form.addRow("Pretty name:", self.entry_pretty)
        self.entry_version = QLineEdit("17.0")
        form.addRow("Odoo version:", self.entry_version)
        self.entry_summary = QLineEdit()
        form.addRow("Summary:", self.entry_summary)
        self.entry_author = QLineEdit()
        form.addRow("Author:", self.entry_author)
        self.entry_model = QLineEdit()
        self.entry_model.setPlaceholderText("library.book (dotted)")
        form.addRow("Model name:", self.entry_model)
        self.text_fields = QTextEdit()
        self.text_fields.setPlaceholderText(
            "One field per line:  name:type  (e.g. name:char)")
        self.text_fields.setMaximumHeight(110)
        form.addRow("Fields:", self.text_fields)
        dest_row = QHBoxLayout()
        self.entry_dest = QLineEdit()
        dest_row.addWidget(self.entry_dest, 1)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(btn_browse)
        form.addRow("Destination folder:", dest_row)
        inst_row = QHBoxLayout()
        self.drop_instance = QComboBox()
        inst_row.addWidget(self.drop_instance, 1)
        self.entry_db = QLineEdit()
        self.entry_db.setPlaceholderText("acceptance database")
        inst_row.addWidget(self.entry_db, 1)
        form.addRow("Acceptance: instance + db:", inst_row)
        self.err = QLabel()
        self.err.setProperty("class", "error")
        self.err.setWordWrap(True)
        form.addRow(self.err)

    def initializePage(self) -> None:  # noqa: N802
        self.drop_instance.clear()
        try:
            instances = list_instances()
        except Exception:
            instances = []
        for inst in instances:
            self.drop_instance.addItem(inst.name, inst.id)
        if instances:
            first = instances[0]
            default_dest = (first.custom_addons_path
                            or f"{first.path}/custom_addons")
            self.entry_dest.setText(default_dest)
            if first.primary_db:
                self.entry_db.setPlaceholderText(
                    f"acceptance database (e.g. {first.primary_db}_test)")

    def _browse_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select destination folder")
        if folder:
            self.entry_dest.setText(folder)

    def definition(self) -> dict:
        fields = []
        for line in self.text_fields.toPlainText().splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, ftype = [p.strip() for p in line.split(":", 1)]
            if name and ftype in FIELD_TYPES:
                fields.append({"name": name, "type": ftype})
        models = []
        if self.entry_model.text().strip():
            models = [{"name": self.entry_model.text().strip(),
                       "description": self.entry_pretty.text().strip()
                       or self.entry_tech.text().strip(),
                       "fields": fields}]
        return {"technical_name": self.entry_tech.text().strip(),
                "pretty_name": self.entry_pretty.text().strip(),
                "odoo_version": self.entry_version.text().strip() or "17.0",
                "summary": self.entry_summary.text().strip(),
                "author": self.entry_author.text().strip(),
                "models": models}

    def validatePage(self) -> bool:  # noqa: N802
        import re

        tech = self.entry_tech.text().strip()
        if not re.match(r"^[a-z_][a-z0-9_]*$", tech):
            return self._fail("Technical name must match ^[a-z_][a-z0-9_]*$.")
        if not self.entry_dest.text().strip():
            return self._fail("Pick a destination folder.")
        if self.drop_instance.count() == 0:
            return self._fail("No instance available for acceptance.")
        db_name = self.entry_db.text().strip()
        if not db_name:
            return self._fail("Acceptance database is required.")
        idx = self.drop_instance.currentIndex()
        instance_id = self.drop_instance.itemData(idx)
        if not self._db_exists(instance_id, db_name):
            return self._fail(
                f"Database '{db_name}' does not exist — create it first "
                "(Databases tab → Init). Acceptance installs for real; "
                "it never auto-creates.")
        self.err.setText("")
        return True

    @staticmethod
    def _db_exists(instance_id: str, db_name: str) -> bool:
        try:
            inst = get_instance(instance_id)
            if inst is None:
                return False
            pw = get_db_password(inst) or None
            return bool(get_db_state(db_name, inst.db_user, pw).exists)
        except Exception:
            return False

    def _fail(self, message: str) -> bool:
        self.err.setText(message)
        return False


class BuildPage(WorkerPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Generate + install")

    def initializePage(self) -> None:  # noqa: N802
        wizard = self.wizard()
        definition = wizard.definition_page.definition()
        dest = wizard.definition_page.entry_dest.text().strip()
        idx = wizard.definition_page.drop_instance.currentIndex()
        instance_id = wizard.definition_page.drop_instance.itemData(idx)
        db_name = wizard.definition_page.entry_db.text().strip()
        self.status_label.setText("Generating…")
        self.progress.setVisible(True)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.progress.setVisible(False)
            if ok:
                self.status_label.setText("Installed cleanly ✓")
                self.set_complete(True)
            else:
                self.status_label.setText("Failed")
                self.append_log(f"ERROR: {message}")

        run_in_background(self, _scaffold_and_install, _done,
                          definition, dest, instance_id, db_name)


def _scaffold_and_install(definition: dict, dest: str, instance_id: str,
                          db_name: str):
    """Generate, then install through the real install flow (acceptance)."""
    from odoo_vite.core import module_manager
    from odoo_vite.core.result import Result

    gen = module_scaffolder.scaffold(definition, dest)
    if not gen.ok:
        return Result.failure(f"Scaffold failed: {gen.message}")
    tech = definition.get("technical_name", "")
    inst = get_instance(instance_id)
    if inst is None:
        return Result.failure("Instance disappeared after scaffold — "
                              "module folder was generated")
    res = module_manager.install_modules(inst, db_name, [tech])
    if not res.ok:
        return Result.failure(
            f"Generated OK, but acceptance install failed: {res.message}")
    return Result.success(
        message=f"Generated {tech} and installed cleanly into '{db_name}'")


class ScaffoldWizard(Wizard):
    """New-module wizard. No result signal needed (message says it all)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Module")
        self.definition_page = DefinitionPage(self)
        self.addPage(self.definition_page)
        self.addPage(BuildPage(self))
        self.setMinimumSize(640, 520)
