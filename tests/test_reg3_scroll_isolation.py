"""REG.3 regression: per-tab scroll isolation (static, GTK-free).

Root cause was one shared Gtk.ScrolledWindow around the whole tab Stack:
the tallest tab (Modules, 1500+ rows) drove the viewport for every tab.
These tests lock the fix: every tab child must go through _scroll_wrap,
and _build_content itself must not construct a shared scroller.
"""

import ast
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "odoo_vite/ui/page_instance_detail.py"


def _build_content_tree():
    tree = ast.parse(PAGE.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "InstanceDetailPage":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_build_content":
                    return sub
    raise AssertionError("_build_content not found")


def _scroll_wrap_def():
    tree = ast.parse(PAGE.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "InstanceDetailPage":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_scroll_wrap":
                    return sub
    return None


def test_every_tab_wrapped_in_own_scroller():
    body = _build_content_tree()
    adds = [n for n in ast.walk(body)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_titled"]
    assert adds, "no tabs registered?"
    for call in adds:
        child = call.args[0]
        wrapped = isinstance(child, ast.Call) and (
            (isinstance(child.func, ast.Name) and child.func.id == "_scroll_wrap")
            or (isinstance(child.func, ast.Attribute)
                and child.func.attr == "_scroll_wrap"))
        assert wrapped, (
            f"tab {call.args[1].value!r} is not wrapped in _scroll_wrap — "
            "REG.3: unwrapped tabs share scroll sizing")


def test_no_shared_page_level_scroller():
    body = _build_content_tree()
    shared = [n for n in ast.walk(body)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "ScrolledWindow"]
    assert not shared, (
        "page-level Gtk.ScrolledWindow found in _build_content — REG.3: "
        "scrolling must live per-tab in _scroll_wrap only")


def test_scroll_wrap_sets_sane_policy():
    fn = _scroll_wrap_def()
    assert fn is not None, "_scroll_wrap helper missing"
    src = ast.dump(fn)
    assert "ScrolledWindow" in src and "set_policy" in src, (
        "_scroll_wrap must construct the ScrolledWindow with a policy")
