"""F2.3 regression: follow-logs must converge on a virtualized ListView
(static, GTK-free).

Findings locked in:
1. Raw vadjustment set_value alone does not stick — ListView lazy layout
   moves the viewport afterwards (observed 5000 -> 3844). The primitive
   must use ListView.scroll_to.
2. A scroll issued while the tab is hidden/unmapped is dropped, stranding
   follow at value 0 forever — the map hook + bounded landing loop must
   exist.
3. Follow is judged against pre-batch geometry (batch-aware slack), and
   each followed batch must converge (landing loop), not fire one blind
   scroll that can stop a row short of flush bottom.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "odoo_vite/ui/page_instance_detail.py"
LOGS_TAB = ROOT / "odoo_vite/ui/detail_tabs/logs.py"


def _funcs(path: Path) -> dict:
    tree = ast.parse(path.read_text())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out[node.name] = node
    return out


def test_scroll_primitive_uses_listview_scroll_to():
    fn = _funcs(PAGE)["_scroll_log_to_end"]
    dump = ast.dump(fn)
    assert "scroll_to" in dump, (
        "F2.3: _scroll_log_to_end must use ListView.scroll_to — raw "
        "vadjustment set_value alone does not survive lazy layout")


def test_landing_loop_bounded_and_reused():
    fns = _funcs(PAGE)
    assert "_ensure_follow_landing" in fns, "landing loop missing"
    dump = ast.dump(fns["_ensure_follow_landing"])
    assert "timeout_add" in dump and "tries" in dump, (
        "F2.3: landing must be a bounded retry loop")
    append_dump = ast.dump(fns["_append_log_lines"])
    assert "_ensure_follow_landing" in append_dump, (
        "F2.3: followed batches must converge via the landing loop, "
        "not a single blind scroll")


def test_log_tab_rehooks_scroll_on_map():
    src = LOGS_TAB.read_text()
    assert '"map"' in src and "_on_log_mapped" in src, (
        "F2.3: logs tab must re-scroll on map (poll often starts hidden)")


def test_batch_aware_follow_slack():
    dump = ast.dump(_funcs(PAGE)["_append_log_lines"])
    assert "slack" in dump or "mean_row" in dump, (
        "F2.3: follow must be judged against pre-batch geometry")
