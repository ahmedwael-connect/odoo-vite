"""GTK4 views/widgets only. No business logic — call into odoo_vite.core.

Shared toolkit flags live here so flow/page modules don't each duplicate
the gi probing (Sprint R refactor).
"""

import gi

gi.require_version("Gtk", "4.0")

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: F401

    HAS_ADW = True
    HAS_NAV_VIEW = hasattr(Adw, "NavigationView")
    HAS_ALERT = hasattr(Adw, "AlertDialog")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_NAV_VIEW = False
    HAS_ALERT = False
