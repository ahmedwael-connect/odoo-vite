"""Qt entry point (PSQ-1.5): `python -m odoo_vite.ui_qt.main_qt`.

Coexists with the GTK `main.py`, which is unchanged and still the default
launch. Run side-by-side to compare during the migration.
"""

import sys

from PySide6.QtWidgets import QApplication

from odoo_vite.ui_qt.main_window import QtMainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    window = QtMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
