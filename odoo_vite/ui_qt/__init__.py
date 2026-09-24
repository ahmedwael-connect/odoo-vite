"""Qt (PySide6) frontend — parallel to odoo_vite/ui/ (GTK), PSQ migration.

Launch: `python -m odoo_vite.ui_qt.main_qt` (the GTK `main.py` is untouched).
Nothing here is imported by the GTK app; see docs/qt-architecture.md.
"""

from odoo_vite.ui_qt.main_window import QtMainWindow  # noqa: F401

__all__ = ["QtMainWindow", "load_qt_style"]


def load_qt_style(app) -> None:
    """Apply ui_qt/qt_style.qss once at startup (PSQ-1.4 tokens)."""
    from pathlib import Path

    qss = (Path(__file__).with_name("qt_style.qss")).read_text()
    app.setStyleSheet(qss)
