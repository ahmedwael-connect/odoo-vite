"""Background core-call helper (PSQ-3.1): the threading-doc pattern as code.

GTK equivalent: daemon thread + GLib.idle_add. Qt version: one-shot
QObject worker on a QThread, results back via queued signal. The worker
touches core/ only; the slot touches widgets only. Never mix the two.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

_LIVE_THREADS: set = set()


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

    def __init__(self, worker: CoreWorker,
                 on_done: Callable[[bool, str, dict], None],
                 parent: QObject | None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._on_done = on_done
        worker.finished.connect(self._deliver)

    @Slot(bool, str, dict)
    def _deliver(self, ok: bool, message: str, data: dict) -> None:
        try:
            self._on_done(ok, message, data)
        finally:
            self._worker.deleteLater()
            self.deleteLater()
        # NOTE: thread.quit() lives on the finished→quit connection below,
        # NOT here — delivery may never run (receiver destroyed first) and
        # the thread must still exit. Verified failure mode: wizard closed
        # mid-provision left the thread in exec() forever.


class BusyTracker(QObject):
    """Ref-counted busy state (GTK _bg_ops parity). Emits only on 0↔n
    transitions so the UI toggles once per busy episode, not per op."""

    changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._count = 0

    @property
    def busy(self) -> bool:
        return self._count > 0

    @Slot()
    def acquire(self) -> None:
        self._count += 1
        if self._count == 1:
            self.changed.emit(True)

    @Slot()
    def release(self) -> None:
        if self._count <= 0:
            return  # never negative, never a spurious transition
        self._count -= 1
        if self._count == 0:
            self.changed.emit(False)


def _tracker_for(host: QObject | None):
    """Nearest BusyTracker up the parent chain (flows reach the window's)."""
    seen = set()
    node = host
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        tracker = getattr(node, "busy_tracker", None)
        if isinstance(tracker, BusyTracker):
            return tracker
        try:
            node = node.parent()
        except Exception:
            return None
    return None


def run_in_background(host: QObject, fn: Callable,
                      on_done: Callable[[bool, str, dict], None],
                      *args, quiet: bool = False,
                      busy: BusyTracker | None = None,
                      **kwargs) -> QThread:
    """One-shot: run core fn off-thread, call on_done(ok, msg, data) on GUI.

    Ownership: thread + delivery parented to `host`, cleaned up on finish.
    Busy accounting (unless quiet): acquire on start, release on finish —
    resolved from `busy` or the nearest BusyTracker up host's parent
    chain (flows reach the main window's). Poll/status refreshes pass
    quiet=True so routine ticks never trip the indicator.
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
    tracker = busy if busy is not None else _tracker_for(host)
    if tracker is not None and not quiet:
        tracker.acquire()
    # Held alive by host parentage (unlike the worker, which needs the
    # thread anchor above) — no reference needed beyond construction.
    _Delivery(worker, on_done, host)
    thread.started.connect(worker.run)
    # Quit rides on finished directly (thread-safe slot), never on
    # delivery — see _deliver's NOTE.
    worker.finished.connect(thread.quit)

    def _finished_release(ok: bool, _message: str, _data: dict) -> None:
        if tracker is not None and not quiet:
            tracker.release()

    worker.finished.connect(_finished_release)
    thread.finished.connect(thread.deleteLater)
    _LIVE_THREADS.add(thread)
    thread.finished.connect(lambda: _LIVE_THREADS.discard(thread))
    thread.start()
    return thread


def wait_for_background(timeout_s: float = 15.0) -> bool:
    """Drain in-flight workers, pumping the loop so queued delivery lands.

    For tests (a QThread destroyed while running aborts the process) and
    app shutdown. Returns False on timeout — the caller decides whether
    that is an error. Never call from inside a worker thread.
    """
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        live = []
        for thread in list(_LIVE_THREADS):
            try:
                if thread.isRunning():
                    live.append(thread)
                else:
                    _LIVE_THREADS.discard(thread)
            except RuntimeError:
                _LIVE_THREADS.discard(thread)  # C++ side already gone
        if not live:
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.02)
    return False
