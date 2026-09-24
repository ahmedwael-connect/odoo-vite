"""Background core-call helper (PSQ-3.1): the threading-doc pattern as code.

GTK equivalent: daemon thread + GLib.idle_add. Qt version: one-shot
QObject worker on a QThread, results back via queued signal. The worker
touches core/ only; the slot touches widgets only. Never mix the two.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot


class CoreWorker(QObject):
    """Runs one core callable off the GUI thread, reports back queued."""

    finished = Signal(bool, str, dict)  # ok, message, data

    def __init__(self, fn: Callable, *args, parent: QObject | None = None,
                 **kwargs) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            res = self._fn(*self._args, **self._kwargs)
            data = res.data if isinstance(res.data, dict) else {}
            self.finished.emit(bool(res.ok), str(res.message), data)
        except Exception as exc:  # core should return Result, never raise
            self.finished.emit(False, f"Unexpected error: {exc}", {})


def run_in_background(host: QObject, fn: Callable,
                      on_done: Callable[[bool, str, dict], None],
                      *args, **kwargs) -> QThread:
    """One-shot: run core fn off-thread, call on_done(ok, msg, data) on GUI.

    Ownership: thread + worker parented to `host`, cleaned up on finish.
    """
    thread = QThread(host)
    worker = CoreWorker(fn, *args, parent=thread, **kwargs)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_done)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
