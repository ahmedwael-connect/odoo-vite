"""Streaming progress dialog (PSQ-5): Qt port of build_progress_dialog.

Long module ops (install/update/uninstall/update-code) stream log lines
while running; Close enables at the end. Thread-safe by construction:
the worker thread calls `request_append.emit(line)` (a signal), which Qt
queues to the GUI-thread `append` slot — the worker never touches the
QTextEdit directly (threading-doc rule).
"""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ProgressDialog(QDialog):
    request_append = Signal(str)
    request_done = Signal(bool, str)

    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        self.status_label = QLabel("Running…")
        layout.addWidget(self.status_label)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFontDatabase.systemFont(
            QFontDatabase.FixedFont))
        layout.addWidget(self.view, 1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)
        self.request_append.connect(self.append)
        self.request_done.connect(self.done)

    @Slot(str)
    def append(self, line: str) -> None:
        self.view.append(line)
        self.status_label.setText(line[-120:])

    @Slot(bool, str)
    def done(self, ok: bool, message: str) -> None:
        self.append(("Done: " if ok else "FAILED: ") + message)
        self.close_btn.setEnabled(True)
