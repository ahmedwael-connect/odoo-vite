"""Wizard pattern (PSQ-9.1): QWizard + validatePage + WorkerPage.

Designed against Create Instance (the most complex wizard), reused for
Adopt and Scaffold. Rules for every wizard in this app:

1. Structure is QWizard with QWizardPage pages — Back/Next/Finish,
   per-page validation in validatePage() (runs on Next), no hand-rolled
   step stack. Data flows forward via the wizard object (page.wizard()
   or explicit accessors), never globals.
2. Long work runs ONLY on the final WorkerPage: a core function in a
   QThread via run_in_background, progress lines streamed through the
   logAppended signal (queued — the worker never touches the QTextEdit),
   Cancel via threading.Event plumbed as the core cancel callback.
3. Failure lands on Retry (re-run; core steps are idempotent and resume)
   + Discard (core discard_draft + close), never a dead error page.
   Success enables Finish and notifies the parent (instanceCreated).
4. Validation that needs I/O (branch lists, system checks) loads async
   on page entry with a loading state — never blocks page display.
"""

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)



class WorkerPage(QWizardPage):
    """Final page base: streaming log + status + cancel + completion.

    Subclass implements start_work() (launch the worker) and on_finished
    (wire Retry/Discard/Finish states). setComplete/completeChanged
    drive the Finish button.
    """

    logAppended = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._complete = False
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)
        self.status_label = QLabel("Ready.")
        layout.addWidget(self.status_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFontFamily("monospace")
        self.log_view.setMinimumHeight(280)
        layout.addWidget(self.log_view, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy until done
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.logAppended.connect(self.append_log)

    @Slot(str)
    def append_log(self, line: str) -> None:
        self.log_view.append(line)

    def isComplete(self) -> bool:  # noqa: N802
        return self._complete

    def set_complete(self, complete: bool) -> None:
        if complete != self._complete:
            self._complete = complete
            self.completeChanged.emit()


class Wizard(QWizard):
    """QWizard with the app's button labels. All app wizards subclass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnLastPage, True)
