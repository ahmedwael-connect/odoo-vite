"""Qt (PySide6) frontend — parallel to odoo_vite/ui/ (GTK), PSQ migration.

Launch: `python -m odoo_vite.ui_qt.main_qt` (the GTK `main.py` is untouched).
Nothing here is imported by the GTK app; see docs/qt-architecture.md.
"""

from odoo_vite.ui_qt.main_window import QtMainWindow  # noqa: F401

__all__ = ["QtMainWindow"]
