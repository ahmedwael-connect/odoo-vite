"""Preferences dialog (U2.1) — the README-promised provisioning-mode switch.

Pure UI: constructed with plain values, returns the user's selection.
The caller (QtMainWindow) does the cheap synchronous settings read/write
on the GUI thread — same as list_instances/get_instance elsewhere.
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class PreferencesDialog(QDialog):
    """Provisioning-mode + environment status. Returns selection via
    selected_mode()."""

    def __init__(self, parent: QWidget | None = None,
                 current_mode: str = "developer",
                 keyring_available: bool = True,
                 db_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        group = QGroupBox("Provisioning mode (applies to new instances)")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(4)
        self.radio_developer = QRadioButton("Developer (default)")
        self.radio_developer.setToolTip(
            "Postgres roles get CREATEDB — creating databases just works.")
        dev_note = QLabel(
            "Postgres roles get CREATEDB — creating databases just works.")
        dev_note.setWordWrap(True)
        self.radio_managed = QRadioButton("Managed (least privilege)")
        self.radio_managed.setToolTip(
            "Roles are created NOCREATEDB — DB create/drop are explicit, "
            "separately-privileged operations.")
        managed_note = QLabel(
            "Least-privilege roles — DB create/drop are explicit, "
            "privileged operations.")
        managed_note.setWordWrap(True)
        group_layout.addWidget(self.radio_developer)
        group_layout.addWidget(dev_note)
        group_layout.addWidget(self.radio_managed)
        group_layout.addWidget(managed_note)
        layout.addWidget(group)

        if (current_mode or "developer") == "managed":
            self.radio_managed.setChecked(True)
        else:
            self.radio_developer.setChecked(True)

        if keyring_available:
            kr_text = "OS keyring: available — passwords stored securely."
        else:
            kr_text = ("OS keyring: NOT available — install gnome-keyring "
                       "(password login, not auto-login) or opt out to "
                       "plaintext explicitly at creation time.")
        kr_label = QLabel(kr_text)
        kr_label.setWordWrap(True)
        layout.addWidget(kr_label)

        if db_path:
            db_label = QLabel(f"Registry: {db_path}")
            db_label.setWordWrap(True)
            db_label.setToolTip(db_path)
            layout.addWidget(db_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_mode(self) -> str:
        return "managed" if self.radio_managed.isChecked() else "developer"
