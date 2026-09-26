"""Application entrypoint (PSQ-10 cutover: Qt is the app now).

Run with:  python -m odoo_vite.main   (or ./main.py from the project root)
"""

import sys

from PySide6.QtWidgets import QApplication

from odoo_vite.ui_qt.main_window import QtMainWindow
from odoo_vite.ui_qt.theme import ThemeMonitor, apply_theme


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    monitor = ThemeMonitor(app)
    apply_theme(app, monitor.dark)
    monitor.themeChanged.connect(lambda dark: apply_theme(app, dark))
    window = QtMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
