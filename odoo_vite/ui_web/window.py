"""pywebview window bootstrap (web cutover Phase 4).

Wires the pieces together:
  - ``js_api``          -> ``ui_web.api.create_api`` (nested domains)
  - push transport      -> ``window.evaluate_js("window.odooVite.push(...)")``
  - file dialog         -> native ``create_file_dialog`` behind ``app.pick_file``
  - URL                 -> Vite dev server (``--dev``/``ODOO_VITE_DEV=1``)
                           or the built ``frontend/dist`` via pywebview's
                           built-in http server

Headless tests never import this module (``tests/test_no_webview_in_core.py``
guards the facade sources; the no-slint/no-Qt guards keep core clean).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

import webview

from odoo_vite.ui_web.api import create_api

DIST = Path(__file__).resolve().parent / "frontend" / "dist" / "index.html"
DEV_URL = "http://localhost:5173"

_FILE_TYPES = {
    "archives": ("Archive (*.tar *.tar.gz *.tgz *.zip)",),
    "dumps": ("SQL dump (*.sql *.dump *.gz)",),
    "all": (),
}


def _file_types(pattern: str) -> tuple[str, ...]:
    """3.1.0 B3: normalize frontend filter hints to pywebview's form.

    Callers pass one of three shapes and all must survive production:
      - a _FILE_TYPES key ("archives", "dumps", "all"),
      - a full expression ("Postgres dumps (*.dump *.sql *.sql.gz)"),
      - bare extension(s) ("tar.gz", "zip", "conf").
    Before this fix only the key shape worked; everything else was
    double-wrapped into a filter that matches nothing.
    """
    if not pattern:
        return ()
    if pattern in _FILE_TYPES:
        return _FILE_TYPES[pattern]
    if "(" in pattern and pattern.rstrip().endswith(")"):
        return (pattern,)
    exts = " ".join(p if p.startswith("*.") else f"*.{p}"
                    for p in pattern.split())
    return (f"Files ({exts})",)


def _pick(window: webview.Window, mode: str, title: str, pattern: str) -> str | None:
    if mode == "folder":
        kind = webview.FileDialog.FOLDER
    elif mode == "save":
        kind = webview.FileDialog.SAVE
    else:
        kind = webview.FileDialog.OPEN
    file_types = _file_types(pattern)
    result = window.create_file_dialog(
        kind,
        allow_multiple=False,
        file_types=file_types,
    )
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result) if result else None


def _transport(window: webview.Window) -> Callable[[dict], None]:
    def send(envelope: dict) -> None:
        # Swallowed by PushChannel if the window is tearing down.
        window.evaluate_js(
            f"window.odooVite && window.odooVite.push({json.dumps(envelope)})"
        )

    return send


def run(dev: bool | None = None) -> int:
    if dev is None:
        dev = os.environ.get("ODOO_VITE_DEV", "") not in ("", "0", "false")

    if dev:
        url = DEV_URL
    elif DIST.exists():
        url = str(DIST)
    else:
        print(
            "frontend/dist missing — run `make frontend-build` "
            "or launch with --dev (Vite dev server).",
            file=sys.stderr,
        )
        return 1

    holder: dict = {}
    api, push = create_api(
        file_dialog=lambda mode, title, pattern: _pick(holder["w"], mode, title, pattern)
    )
    window = webview.create_window(
        "Odoo Vite",
        url,
        js_api=api,
        width=1360,
        height=860,
        min_size=(960, 640),
    )
    holder["w"] = window
    push.set_transport(_transport(window))

    webview.start(http_server=not dev)
    # Watch sessions hold observer threads — shut them down on the way out.
    try:
        api.watch.stop_all()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(run(dev="--dev" in sys.argv))
