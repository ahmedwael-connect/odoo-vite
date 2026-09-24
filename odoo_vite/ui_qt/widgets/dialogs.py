"""Shared dialog patterns (PSQ-2.2) — mirrors the app's confirmation tiers.

- ask_confirm(): light confirm (Cancel / <label>), main thread, bool.
- ask_confirm_typed(): destructive tier — the user retypes an expected
  string (managed remove, DB drop, restore-into-existing), same rule as
  the GTK build. OK enables only on exact match.
Both are plain QDialogs with the design-system spacing; destructive OK
buttons carry the `destructive-action` role via dynamic property
`role="destructive"` (QSS matches [role="destructive"]).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


def _base_dialog(parent: QWidget | None, heading: str, body: str,
                 ok_label: str, destructive: bool) -> tuple[QDialog, QDialogButtonBox]:
    dlg = QDialog(parent)
    dlg.setWindowTitle(heading)
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(8)
    layout.setContentsMargins(12, 12, 12, 12)
    if body:
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        layout.addWidget(body_lbl)
    buttons = QDialogButtonBox(
        QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
    buttons.button(QDialogButtonBox.Ok).setText(ok_label)
    if destructive:
        buttons.button(QDialogButtonBox.Ok).setProperty("role", "destructive")
    layout.addWidget(buttons)
    return dlg, buttons


def ask_confirm(parent: QWidget | None, heading: str, body: str = "",
                confirm_label: str = "Confirm",
                destructive: bool = False) -> bool:
    """Main-thread modal confirm. Returns True iff confirmed."""
    dlg, buttons = _base_dialog(parent, heading, body, confirm_label,
                                destructive)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    return dlg.exec() == QDialog.Accepted


def typed_gate_ok(text: str, expected: str) -> bool:
    """Exact-match rule for the destructive tier (unit-tested)."""
    return text.strip() == expected


def ask_confirm_typed(parent: QWidget | None, heading: str, body: str,
                      expected: str, confirm_label: str = "Delete") -> bool:
    """Destructive tier: OK enables only when the entry matches exactly."""
    dlg, buttons = _base_dialog(parent, heading, body, confirm_label,
                                destructive=True)
    entry = QLineEdit()
    entry.setPlaceholderText(f"Type {expected!r} to confirm")
    layout = dlg.layout()
    assert layout is not None
    layout.insertWidget(layout.count() - 1, entry)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    ok_btn.setEnabled(False)
    entry.textChanged.connect(
        lambda text: ok_btn.setEnabled(typed_gate_ok(text, expected)))
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    return dlg.exec() == QDialog.Accepted
