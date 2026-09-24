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


class _Delivery(QObject):
    """Lives on the GUI thread; re-emits worker results there.

    A plain-Python on_done connected directly would run in the WORKER
    thread (AutoConnection has no QObject affinity to key off). This
    forwarder gives Qt a GUI-thread receiver, so delivery is queued.
    """

    def __init__(self, thread: QThread, worker: CoreWorker,
                 on_done: Callable[[bool, str, dict], None],
                 parent: QObject | None) -> None:
        super().__init__(parent)
        self._thread = thread
        self._worker = worker
        self._on_done = on_done
        worker.finished.connect(self._deliver)

    @Slot(bool, str, dict)
    def _deliver(self, ok: bool, message: str, data: dict) -> None:
        try:
            self._on_done(ok, message, data)
        finally:
            self._thread.quit()
            self._worker.deleteLater()
            self.deleteLater()


def run_in_background(host: QObject, fn: Callable,
                      on_done: Callable[[bool, str, dict], None],
                      *args, **kwargs) -> QThread:
    """One-shot: run core fn off-thread, call on_done(ok, msg, data) on GUI.

    Ownership: thread + delivery parented to `host`, cleaned up on finish.
    """
    thread = QThread(host)
    # NOTE: worker must have NO parent — moveToThread refuses parented
    # objects (verified warning live).
    worker = CoreWorker(fn, *args, **kwargs)
    # NOTE: PySide6 connections do NOT keep the receiver/callable alive —
    # without this anchor the worker is GC'd the moment this function
    # returns (verified: slot never fires) and the thread then dies
    # still-running at teardown. The anchor dies with the thread, no leak.
    thread._owned_worker = worker
    worker.moveToThread(thread)
    delivery = _Delivery(thread, worker, on_done, host)
    thread.started.connect(worker.run)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
