"""Application entrypoint (web cutover Phase 5: pywebview + React).

Run with:  python3 main.py   (or ./main.py / python3 -m odoo_vite.main)

``--dev`` points the window at the Vite dev server (localhost:5173 —
start it first with ``make frontend-dev``); without it the window serves
the built ``frontend/dist`` (``make frontend-build``). ``ODOO_VITE_DEV=1``
works too.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    dev: bool | None = None
    if "--dev" in args:
        dev = True
    elif "--prod" in args:
        dev = False

    from odoo_vite.ui_web.window import run

    return run(dev=dev)


if __name__ == "__main__":
    raise SystemExit(main())
