"""Pure-Python backend logic. Framework-agnostic: zero GUI imports allowed here.

UI code must never import GUI toolkits transitively through this package.
Long-running operations run in background threads; results cross to the
Qt main thread via queued signals on the UI side (see
odoo_vite.ui_qt.workers.run_in_background; core/events.py is sync only).
"""

from odoo_vite.core.result import Result

__all__ = ["Result"]
