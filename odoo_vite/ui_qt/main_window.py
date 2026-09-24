"""Qt main window shell (PSQ-1.2): header, sidebar, stacked content area.

Same information architecture as the GTK MainWindow, no redesign:
sidebar (instance list) left, content right. Sidebar rows come from the
real, unmodified `registry.list_instances()` — the core-reuse proof.
Styling arrives in PSQ-1.4; threading discipline in PSQ-1.3.
"""

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.core.registry import list_instances
from odoo_vite.core.version import __version__


class QtMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Odoo Vite v{__version__} (Qt)")
        self.resize(1000, 660)

        toolbar = QToolBar("Main")
        toolbar.addWidget(QLabel(f"Odoo Vite  v{__version__}"))
        self.addToolBar(toolbar)

        splitter = QSplitter()
        self.setCentralWidget(splitter)

        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(320)
        splitter.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.addWidget(QLabel("Select an instance (content pages land in PSQ-3+)"))
        layout.addStretch(1)
        self.stack.addWidget(placeholder)
        splitter.addWidget(self.stack)
        splitter.setSizes([260, 740])

        self.refresh_sidebar()

    def refresh_sidebar(self) -> None:
        """Instance rows from the real registry — core reuse proof."""
        self.sidebar.clear()
        try:
            instances = list_instances()
        except Exception:
            instances = []
        if not instances:
            self.sidebar.addItem("(no instances yet)")
            return
        for inst in instances:
            self.sidebar.addItem(f"{inst.name}  ·  {inst.status}")
