"""Streaming progress dialog (PSQ-5): Qt port of build_progress_dialog.

Long module ops (install/update/uninstall/update-code) stream log lines
while running; Close enables at the end. Cancel sets a threading.Event
the worker polls via core's `cancel` callback — the op stops at the next
stage boundary (or mid-subprocess, killed). Thread-safe by construction:
the worker thread calls `request_append.emit(line)` (a signal), which Qt
queues to the GUI-thread `append` slot — the worker never touches the
QTextEdit directly (threading-doc rule).
"""

import threading

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
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
        # Polled by core via the `cancel` callback — never touched by Qt.
        self.cancel_event = threading.Event()
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
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip(
            "Stop at the next stage (kills the running step)")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)
        self.request_append.connect(self.append)
        self.request_done.connect(self.done)

    @Slot()
    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling… (finishing current step)")

    @Slot(str)
    def append(self, line: str) -> None:
        self.view.append(line)
        self.status_label.setText(line[-120:])

    @Slot(bool, str)
    def done(self, ok: bool, message: str) -> None:
        self.append(("Done: " if ok else "FAILED: ") + message)
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
