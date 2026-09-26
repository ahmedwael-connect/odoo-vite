"""Qt flows package (PSQ-3+): one module per feature area, mirroring ui/.
"""

from odoo_vite.ui_qt.flows.configuration import (  # noqa: F401
    ConfigurationFlows,
)
from odoo_vite.ui_qt.flows.databases import DatabaseFlows  # noqa: F401
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows  # noqa: F401
from odoo_vite.ui_qt.flows.modules import ModuleFlows  # noqa: F401

__all__ = ["ConfigurationFlows", "DatabaseFlows", "LifecycleFlows",
           "ModuleFlows"]
