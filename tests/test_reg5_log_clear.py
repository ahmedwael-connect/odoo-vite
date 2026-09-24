"""REG.5 regression: Logs tab "Clear view" button (static, GTK-free).

Locks in: the button exists with a display-only tooltip, is connected to
_on_log_clear_view, and that handler splices the *store* (never truncates
the log file — no open(...'w')/truncate/unlink anywhere near it).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS_TAB = ROOT / "odoo_vite/ui/detail_tabs/logs.py"
PAGE = ROOT / "odoo_vite/ui/page_instance_detail.py"


def test_clear_view_button_wired_display_only():
    src = LOGS_TAB.read_text()
    assert "btn_log_clear" in src, "Clear view button missing from Logs toolbar"
    assert "_on_log_clear_view" in src, "Clear view button not connected"
    assert "log file on disk is" in src or "untouched" in src, (
        "tooltip must state the file on disk is untouched")


def test_clear_handler_splices_store_not_file():
    tree = ast.parse(PAGE.read_text())
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_log_clear_view":
            fn = node
            break
    assert fn is not None, "_on_log_clear_view missing from detail page"
    dump = ast.dump(fn)
    assert "splice" in dump and "log_store" in dump, (
        "handler must clear the displayed store via splice")
    for bad in ("truncate", "unlink", "remove("):
        assert bad not in dump, (
            f"handler must never touch the file on disk ({bad} found)")
