"""Pure-Python backend logic. Framework-agnostic: zero GTK imports allowed here.

UI code must never import Gtk/Adw transitively through this package.
Long-running operations run in background threads; results cross to the
GTK main thread via GLib.idle_add() on the UI side (see core/events.py).
"""

from odoo_vite.core.result import Result

__all__ = ["Result"]
