#!/usr/bin/env python3
"""Capture a screenshot of the running app (X11).

Usage:
    python3 tools/shots.py <label>          # -> docs/shots/<ts>-<label>.png
    DISPLAY=:2 python3 tools/shots.py <label>

Requires Pillow (uses X11 xwd fallback or xcb). The window layout itself
is controlled manually (no programmatic resize/tab-switch); resize the
window between runs and capture at 800/1100/1400.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "shots"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="shot label, e.g. rm0-baseline-dark")
    parser.add_argument("--display", default=None, help="X display, e.g. :1")
    args = parser.parse_args()

    try:
        from PIL import ImageGrab
    except ImportError:
        print("Pillow required: pip install pillow", file=sys.stderr)
        return 1

    import os

    display = args.display or os.environ.get("DISPLAY")
    if not display:
        print("no DISPLAY; pass --display :1", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT / f"{stamp}-{args.label}.png"
    ImageGrab.grab(xdisplay=display).save(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
