"""Framework-free ops classes (web cutover Phase 5).

One ops class per UI domain (lifecycle, databases, modules,
configuration, logs, devtools, wizards, transfer) — async drivers over
``core/`` with the same shape both frontends shared: plain Python data
in, ``Result``/dicts out, progress via callback. Formerly housed in
``odoo_vite.ops/``; moved here when the Slint frontend was deleted.

Layering: this package imports only ``odoo_vite.core`` — never
``ui_web``, never a GUI toolkit (``tests/test_no_slint_in_core.py`` and
``tests/test_no_webview_in_core.py`` enforce it).
"""
