"""pywebview + React frontend (web cutover).

Layering:
- ``core/`` stays GUI-free — this package never adds GUI imports there
  (``tests/test_no_webview_in_core.py`` enforces it).
- ``api.py`` wraps the framework-free ops classes in ``odoo_vite/ops/``
  (imported only for method dispatch; no slint, no toolkit of any kind).
- pywebview itself is only imported by the window bootstrap (window.py);
  ``api.py`` and ``push.py`` run headless in tests.

Run: ``python3 main.py`` (add ``--dev`` for the Vite dev server).
"""

from odoo_vite.ui_web.api import Api, create_api
from odoo_vite.ui_web.push import ListTransport, PushChannel

__all__ = ["Api", "create_api", "ListTransport", "PushChannel"]
