"""System theme following (PSQ-10): XDG desktop portal, no new deps.

GTK followed the GNOME theme automatically; Qt does not. This module
reads org.gnome.desktop.interface color-scheme via the portal (uint32:
0 = no preference, 1 = prefer dark, 2 = prefer light), falls back to
gsettings, then to light. A monitor re-emits on live changes so the app
tracks the desktop without restart. The human pass proved bare
environments resolve palette(mid) to white — fixed grays stay for dim
roles in BOTH variants; only base surfaces/text flip.
"""

from PySide6.QtCore import QObject, Signal

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Settings"
NAMESPACE = "org.gnome.desktop.interface"
KEY = "color-scheme"


def dark_from_value(value) -> bool:
    """Portal uint32 (or gsettings string) -> prefer-dark? Pure, tested."""
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        pass
    return str(value or "").strip().strip("'\"") in (
        "prefer-dark", "dark", "1")


def _read_portal():
    from PySide6.QtDBus import QDBusConnection, QDBusInterface

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    iface = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, PORTAL_IFACE, bus)
    if not iface.isValid():
        return None
    reply = iface.call("Read", NAMESPACE, KEY)
    if reply.type() == reply.ErrorMessage or not reply.arguments():
        return None
    return reply.arguments()[0]


def _read_gsettings():
    import shutil
    import subprocess

    if shutil.which("gsettings") is None:
        return None
    try:
        proc = subprocess.run(
            ["gsettings", "get", NAMESPACE, "color-scheme"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def system_prefers_dark() -> bool:
    """True if the desktop prefers dark. Never raises (light on doubt)."""
    try:
        value = _read_portal()
        if value is not None:
            return dark_from_value(value)
    except Exception:
        pass
    try:
        value = _read_gsettings()
        if value is not None:
            return dark_from_value(value)
    except Exception:
        pass
    return False


class ThemeMonitor(QObject):
    """Re-emits when the desktop color-scheme changes. App-lifetime."""

    themeChanged = Signal(bool)  # True == dark now

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dark = system_prefers_dark()
        self._bus_ok = self._subscribe()

    @property
    def dark(self) -> bool:
        return self._dark

    def _subscribe(self) -> bool:
        try:
            from PySide6.QtDBus import QDBusConnection
        except Exception:
            return False
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return False
            return bus.connect(
                PORTAL_SERVICE, PORTAL_PATH, PORTAL_IFACE, "SettingChanged",
                self._on_setting_changed)
        except Exception:
            return False

    def _on_setting_changed(self, namespace: str, key: str,
                            value) -> None:
        if namespace == NAMESPACE and key == KEY:
            dark = dark_from_value(value)
            if dark != self._dark:
                self._dark = dark
                self.themeChanged.emit(dark)


def apply_theme(app, dark: bool) -> None:
    """Load the light or dark QSS variant. Safe to call repeatedly."""
    from pathlib import Path

    name = "qt_style_dark.qss" if dark else "qt_style.qss"
    qss = (Path(__file__).with_name(name)).read_text()
    app.setStyleSheet(qss)
