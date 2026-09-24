# Qt Architecture (PSQ-1.3+)

The Qt frontend (`odoo_vite/ui_qt/`) parallels the GTK one (`odoo_vite/ui/`)
during the migration. `core/` is shared untouched by both. Rules below are
enforced by tests where noted — read them as law, not advice.

## Threading discipline (the `idle_add` equivalent)

GTK rule, unchanged in spirit: **never touch UI objects from a background
thread; marshal results back to the main thread.** The Qt mechanism is
different — queued signal/slot connections instead of `GLib.idle_add`:

- A `QObject` worker lives on a `QThread`. It calls pure-`core/` functions
  (which return `Result`) and emits a `finished(Result-like payload)`
  signal. Qt delivers a signal emitted from a worker thread to a slot on
  the main thread automatically **when the connection is queued** (the
  default `Qt.AutoConnection` does exactly this for cross-thread
  signal/slot pairs — do NOT force `DirectConnection`).
- The main-thread slot then updates widgets. No widget access inside the
  worker — same as "no Gtk calls off the main loop" in the GTK app.

```python
from PySide6.QtCore import QObject, QThread, Signal, Slot

class BackupWorker(QObject):
    finished = Signal(bool, str)  # ok, message (from core Result)

    def __init__(self, schedule_id: str):
        super().__init__()
        self._schedule_id = schedule_id

    @Slot()
    def run(self) -> None:
        from odoo_vite.core import backup_scheduler
        # core-only here: no widgets, no QApplication access.
        res = backup_scheduler.run_schedule(self._schedule_id)
        self.finished.emit(res.ok, res.message)  # queued to main thread

# Main-thread side (e.g. a flows controller):
self._thread = QThread()
self._worker = BackupWorker(schedule_id)
self._worker.moveToThread(self._thread)
self._thread.started.connect(self._worker.run)
self._worker.finished.connect(self._on_backup_done)  # main-thread slot
self._worker.finished.connect(self._thread.quit)
self._thread.start()

@Slot(bool, str)
def _on_backup_done(self, ok: bool, message: str) -> None:
    ...  # widget updates are safe here
```

Lifetime rule: keep a reference to both `QThread` and worker for the
operation's duration (parent them or store on the controller); connect
`finished` to `thread.quit` + `deleteLater` cleanup. One-shot per
operation — same as the GTK app's one-daemon-thread-per-flow pattern.

## Layering

- `core/` imports neither `gi` nor `PySide6`/`shiboken6`
  (`tests/test_no_gtk_in_core.py`, `tests/test_no_pyside_in_core.py`).
- `ui_qt/` may import `core/` only. No `ui/` (GTK) imports from `ui_qt/`
  and vice versa — PSQ-10's deletion must be a directory removal.
- Long operations run on `QThread` workers; the GUI thread is never blocked.

## Theming

See `docs/design-system.md` (shared scale/roles) + `odoo_vite/ui_qt/qt_style.qss`.
Theme-following decision: system theme via XDG desktop portal (PSQ-1.4
recommendation) — details in that ticket's report.

## Headless testing

`QT_QPA_PLATFORM=offscreen` (no X server needed). Do NOT use real widget
construction in tests that must run display-free — same caution as GTK.
`pytest-qt`: adopted from PSQ-1.6 (see ticket report for reasoning).

## Environment note (no root, user-space Qt deps)

PySide6 ships its own Qt binaries but needs `libxcb-cursor.so.0` for the
`xcb` platform plugin, absent on stock Ubuntu 24.04 and uninstallable
without root. Workaround used here: `apt download libxcb-cursor0` +
`dpkg-deb -x` into `~/.local`, then
`LD_LIBRARY_PATH=$HOME/.local/usr/lib/x86_64-linux-gnu` when launching
under X/Xvfb. Offscreen needs no workaround. Revisit if deployment ever
gets proper system packages.
