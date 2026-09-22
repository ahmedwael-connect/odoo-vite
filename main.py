#!/usr/bin/env python3
"""Root launcher shim — allows the ticket's `python main.py` invocation.

Canonical entrypoint is odoo_vite.main; this file only adjusts sys.path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from odoo_vite.main import main

if __name__ == "__main__":
    raise SystemExit(main())
