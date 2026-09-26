"""Qt entry point alias (PSQ-1.5 dev alias, kept working post-cutover).

Canonical entrypoint is odoo_vite.main; this module forwards to it.
`python -m odoo_vite.ui_qt.main_qt` still works for muscle memory.
"""

import sys

from odoo_vite.main import main

if __name__ == "__main__":
    sys.exit(main())
