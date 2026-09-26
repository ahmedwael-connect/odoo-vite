"""Qt wizards package (PSQ-9): one module per wizard, pattern in widgets/.
"""

from odoo_vite.ui_qt.wizards.adopt_instance import AdoptWizard  # noqa: F401
from odoo_vite.ui_qt.wizards.create_instance import (  # noqa: F401
    CreateWizard,
)
from odoo_vite.ui_qt.wizards.scaffold_module import ScaffoldWizard  # noqa: F401

__all__ = ["AdoptWizard", "CreateWizard", "ScaffoldWizard"]
