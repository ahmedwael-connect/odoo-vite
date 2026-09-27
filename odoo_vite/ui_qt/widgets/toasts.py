"""Toast notifications (PSQ-2.2): transient success/info feedback.

Qt has no built-in toast: a frameless auto-hiding label pinned to the
bottom-center of a host widget, matching the GTK app's "toasts for
resolved/transient events" rule. Persistent/bad states stay as labels —
this helper is not for those.
"""

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class Toaster(QLabel):
    _live: list["Toaster"] = []

    def __init__(self, host: QWidget, message: str,
                 timeout_ms: int = 4000) -> None:
        super().__init__(host)
        self._timeout_ms = timeout_ms
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setProperty("role", "toast")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)
        self._show_message(message)
        Toaster._live.append(self)

    def _show_message(self, message: str) -> None:
        self.setText(message)
        self.setMargin(12)
        self.adjustSize()
        host = self.parent()
        try:
            host_rect = host.rect()
            x = host_rect.center().x() - self.width() // 2
            y = host_rect.bottom() - self.height() - 24
            self.move(host.mapToGlobal(QPoint(max(0, x), max(0, y))))
        except Exception:
            pass
        self.show()
        self._timer.start(self._timeout_ms)

    def _expire(self) -> None:
        try:
            Toaster._live.remove(self)
        except ValueError:
            pass
        self.close()
        self.deleteLater()

    @staticmethod
    def _prune() -> None:
        for toast in list(Toaster._live):
            try:
                if toast.parent() is None:
                    Toaster._live.remove(toast)
            except (RuntimeError, ValueError):
                try:
                    Toaster._live.remove(toast)
                except ValueError:
                    pass

    @staticmethod
    def show_text(host: QWidget, message: str,
                  timeout_ms: int = 4000) -> "Toaster":
        """Fire-and-forget toast. Returns the widget for tests.

        Coalesced per host: rapid messages update one toast and restart
        its timer instead of stacking labels (S4).
        """
        Toaster._prune()
        for toast in list(Toaster._live):
            try:
                if toast.parent() is host:
                    toast._timeout_ms = timeout_ms
                    toast._show_message(message)
                    return toast
            except RuntimeError:
                continue
        return Toaster(host, message, timeout_ms)
