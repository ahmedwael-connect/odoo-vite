"""Qt views package (PSQ-3+): one module per tab/page, mirroring ui_qt/flows/.
"""

from odoo_vite.ui_qt.views.configuration import (  # noqa: F401
    ConfigurationPage,
)
from odoo_vite.ui_qt.views.databases import DatabasesPage  # noqa: F401
from odoo_vite.ui_qt.views.devtools import DevToolsPage  # noqa: F401
from odoo_vite.ui_qt.views.logs import LogsPage  # noqa: F401
from odoo_vite.ui_qt.views.modules import ModulesPage  # noqa: F401
from odoo_vite.ui_qt.views.overview import OverviewPage  # noqa: F401
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar  # noqa: F401

__all__ = ["ConfigurationPage", "DatabasesPage", "DevToolsPage",
           "InstanceSidebar", "LogsPage", "ModulesPage", "OverviewPage"]
