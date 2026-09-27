"""Qt flows package (PSQ-3+): one module per feature area, mirroring ui/.
"""

from odoo_vite.ui_qt.flows.backup_schedules import (  # noqa: F401
    BackupSchedulesFlows,
)
from odoo_vite.ui_qt.flows.configuration import (  # noqa: F401
    ConfigurationFlows,
)
from odoo_vite.ui_qt.flows.databases import DatabaseFlows  # noqa: F401
from odoo_vite.ui_qt.flows.dev_tools_process import (  # noqa: F401
    DevToolsProcessFlows,
)
from odoo_vite.ui_qt.flows.dev_tools_rpc import DevToolsRpcFlows  # noqa: F401
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows  # noqa: F401
from odoo_vite.ui_qt.flows.logs import LogFlows  # noqa: F401
from odoo_vite.ui_qt.flows.modules import ModuleFlows  # noqa: F401

__all__ = ["BackupSchedulesFlows", "ConfigurationFlows", "DatabaseFlows",
           "DevToolsProcessFlows", "DevToolsRpcFlows", "LifecycleFlows",
           "LogFlows", "ModuleFlows"]
