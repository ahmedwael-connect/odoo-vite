"""Application entrypoint (PSS-9 cutover: Slint is the app now).

Run with:  python -m odoo_vite.main   (or ./main.py from the project root)

Thread-safety (docs/slint-thread-safety.md): slint-python still frees
Slint values on whichever thread runs cyclic GC, which aborts the
process when that thread isn't the UI thread. Automatic GC therefore
stays OFF for the whole session (refcounting still frees acyclic trash
immediately); the bridge collects explicitly on the owner thread at
quiescence (dialog release, teardown). Memory grows only with true
cycles between collects — bounded and observable, unlike a SIGABRT.
"""

import gc


def main(argv: list[str] | None = None) -> int:
    del argv
    gc.disable()
    try:
        from odoo_vite.ui_slint.bridge import SlintBridge

        bridge = SlintBridge()
        try:
            bridge.run()
        finally:
            bridge.close()
    finally:
        gc.enable()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
