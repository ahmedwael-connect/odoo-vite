"""Qt views package (PSQ-3+): one module per tab/page, mirroring ui_qt/flows/.
"""

from odoo_vite.ui_qt.views.databases import DatabasesPage  # noqa: F401
from odoo_vite.ui_qt.views.overview import OverviewPage  # noqa: F401
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar  # noqa: F401

__all__ = ["DatabasesPage", "InstanceSidebar", "OverviewPage"]
