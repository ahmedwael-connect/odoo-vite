"""PSQ-4 guardrail: nested callbacks must be referenced (dead-callback check).

Regression class: main_window._discover_dialog defined _on_ready but never
called/passed it — the flow silently never ran. Static AST check over
ui_qt: every nested def inside a method must be loaded somewhere in that
method (called, connected, scheduled, or returned).
"""

import ast
import builtins
import dis
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "odoo_vite" / "ui_qt"
FILES = sorted((ROOT).rglob("*.py"))


def _dead_callbacks(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    dead = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method,
                              (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nested = {n.name for n in ast.walk(method)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and n is not method}
            if not nested:
                continue
            loaded = set()
            for sub in ast.walk(method):
                if isinstance(sub, ast.Name) and isinstance(
                        sub.ctx, ast.Load):
                    loaded.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    loaded.add(sub.attr)
            for name in nested:
                if name not in loaded:
                    dead.append(f"{method.name}/{name}")
    return dead


def test_no_dead_nested_callbacks():
    problems = {}
    for path in FILES:
        miss = _dead_callbacks(path)
        if miss:
            problems[path.name] = miss
    assert not problems, f"unreferenced nested callbacks: {problems}"


def test_no_unbound_globals_in_ui_qt():
    """PSQ-4 follow-up: bare names (e.g. track_database after an import
    hoist dropped it) must resolve at module level. Same dis-based check
    as test_ui_flow_imports, applied to ui_qt."""
    problems = {}
    for path in FILES:
        src = path.read_text()
        tree = ast.parse(src, filename=str(path))
        code = compile(src, str(path), "exec")
        defined: set[str] = set()

        def _collect(node, top: bool = False):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    for alias in child.names:
                        defined.add(
                            (alias.asname or alias.name).split(".")[0])
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef)):
                    defined.add(child.name)
                elif isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            defined.add(target.id)
                elif isinstance(child, ast.AnnAssign) and isinstance(
                        child.target, ast.Name):
                    defined.add(child.target.id)
                _collect(child)

        _collect(tree)
        defined |= set(vars(builtins))
        defined |= {"__file__", "__name__", "__doc__", "__package__",
                    "__annotations__", "__spec__", "__loader__"}

        def _codes(code_obj):
            yield code_obj
            for const in code_obj.co_consts:
                if isinstance(const, types.CodeType):
                    yield from _codes(const)

        missing = set()
        for nested in _codes(code):
            for instr in dis.get_instructions(nested):
                if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    if instr.argval not in defined:
                        missing.add(instr.argval)
        if missing:
            problems[path.name] = sorted(missing)
    assert not problems, f"unbound globals in ui_qt: {problems}"
