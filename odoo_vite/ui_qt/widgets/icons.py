"""Button icons + hierarchy (Part B.4 + B.3.1): one curated source.

All icons come from QStyle.StandardPixmap (platform-consistent, never a
mix of bundled styles). Primary actions also get setDefault(true), which
QSS renders with an accent border — the visual hierarchy rule from B.3:
primary distinct from secondary, destructive in red.
"""

from PySide6.QtWidgets import QPushButton, QStyle

ICONS = {
    "start": QStyle.SP_MediaPlay,
    "stop": QStyle.SP_MediaStop,
    "restart": QStyle.SP_BrowserReload,
    "remove": QStyle.SP_TrashIcon,
    "delete": QStyle.SP_TrashIcon,
    "drop": QStyle.SP_TrashIcon,
    "uninstall": QStyle.SP_TrashIcon,
    "save": QStyle.SP_DialogSaveButton,
    "apply": QStyle.SP_DialogApplyButton,
    "new": QStyle.SP_FileDialogNewFolder,
    "add": QStyle.SP_ArrowForward,
    "connect": QStyle.SP_CommandLink,
    "run": QStyle.SP_MediaPlay,
}


def style_button(btn: QPushButton, icon: str = "",
                 primary: bool = False) -> QPushButton:
    """Attach a curated icon and/or primary (default-button) treatment."""
    if icon:
        pixmap = ICONS.get(icon)
        if pixmap is not None:
            try:
                btn.setIcon(btn.style().standardIcon(pixmap))
            except Exception:
                pass
    if primary:
        btn.setDefault(True)
    return btn
