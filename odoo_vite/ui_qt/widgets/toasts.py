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
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setProperty("role", "toast")
        self.setText(message)
        self.setMargin(12)
        self.adjustSize()
        host_rect = host.rect()
        x = host_rect.center().x() - self.width() // 2
        y = host_rect.bottom() - self.height() - 24
        self.move(host.mapToGlobal(QPoint(max(0, x), max(0, y))))
        self.show()
        Toaster._live.append(self)
        QTimer.singleShot(timeout_ms, self._expire)

    def _expire(self) -> None:
        try:
            Toaster._live.remove(self)
        except ValueError:
            pass
        self.close()
        self.deleteLater()

    @staticmethod
    def show_text(host: QWidget, message: str,
                  timeout_ms: int = 4000) -> "Toaster":
        """Fire-and-forget toast. Returns the widget for tests."""
        return Toaster(host, message, timeout_ms)
