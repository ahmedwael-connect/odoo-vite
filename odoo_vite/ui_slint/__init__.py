"""Slint frontend (PSS-1 foundation): Qt-free parallel UI over core/.

Coexistence rule (migration-retro §1): ui_slint/ parallels ui_qt/ until
the Slint cutover. core/ is untouched and shared. Run with:
    python3 -m odoo_vite.ui_slint        (Slint app)
    python3 main.py                      (Qt app, unchanged)
"""

from odoo_vite.ui_slint.bridge import SlintBridge, main

__all__ = ["SlintBridge", "main"]
