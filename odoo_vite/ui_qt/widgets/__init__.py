"""Shared Qt component library (PSQ-2). Selection widget first.

Built once, from the GTK bug record — not once per screen:
- unclear checkbox states  -> checked rows are bold + accent-tinted and
  carry a real CheckState in the model, never hover-only styling
- H-P1 unbounded lists   -> max_visible_rows height cap built in
- long-string overflow    -> title elided (Qt.ElideMiddle), full text in
  the tooltip
- 1500-row module lists  -> QListView + model (virtualized), never one
  widget per row
"""

from odoo_vite.ui_qt.widgets.dialogs import (  # noqa: F401
    ask_confirm,
    ask_confirm_typed,
    typed_gate_ok,
)
from odoo_vite.ui_qt.widgets.selection_list import SelectionList  # noqa: F401
from odoo_vite.ui_qt.widgets.toasts import Toaster  # noqa: F401

__all__ = ["SelectionList", "Toaster", "ask_confirm", "ask_confirm_typed",
           "typed_gate_ok"]
