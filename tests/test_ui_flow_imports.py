"""Sprint R guardrail: every global a UI flow/tab function references must
be defined (import or module-level def). Static analysis only — no `import`
of the modules under test, so keyring/gi sys.modules pollution from other
tests cannot make this flaky. Catches mechanical-move misses like a missing
`Adw`/`Path`/`Pango`/`process_manager` import that only explode at dialog
time — after the unit suite is already green."""

import ast
import builtins
import dis
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW_FILES = sorted((ROOT / "odoo_vite/ui/flows").glob("*.py")) + sorted(
    (ROOT / "odoo_vite/ui/detail_tabs").glob("*.py"))
FLOW_FILES = [p for p in FLOW_FILES if p.name != "__init__.py"]


def _all_code(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _all_code(const)


def _module_defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.If):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
        elif isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
    return names


def _global_loads(code: types.CodeType):
    for instr in dis.get_instructions(code):
        if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
            yield instr.argval


def _missing_globals(path: Path) -> set[str]:
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    code = compile(src, str(path), "exec")
    defined = _module_defined_names(tree) | set(vars(builtins))
    missing: set[str] = set()
    # Only function/class bodies of this file — skip nested local imports
    # by walking every code object's LOAD_GLOBAL (locals are LOAD_FAST /
    # LOAD_DEREF; function-level imports bind as locals).
    for nested in _all_code(code):
        for name in _global_loads(nested):
            if name not in defined:
                missing.add(name)
    # Module top-level also uses LOAD_NAME/LOAD_GLOBAL — already covered.
    return missing


def test_flow_files_reference_only_defined_globals():
    problems = {}
    for path in FLOW_FILES:
        miss = _missing_globals(path)
        if miss:
            problems[path.name] = sorted(miss)
    assert not problems, f"unbound globals in UI flows: {problems}"


def test_progress_dialog_helper_is_module_level():
    src = (ROOT / "odoo_vite/ui/flows/dialogs.py").read_text()
    tree = ast.parse(src)
    top_level_defs = {
        n.name for n in tree.body if isinstance(n, ast.FunctionDef)
    }
    assert "build_progress_dialog" in top_level_defs, (
        "build_progress_dialog must be a module-level def in flows.dialogs "
        "(regression: it once nested silently inside filter_checks)"
    )


def test_no_bare_self_dialog_parents():
    """REG.1: dialog parents must be `self.win`, never bare `self`.

    The R.5 move rewrote `self.X` attribute access but left bare `self`
    passed as a dialog parent (`dlg.choose(self, ...)`), which breaks the
    call silently (button "does nothing"). Whitespace-collapsed so
    multi-line calls (e.g. `select_folder(\\n self, ...)`) are caught too.
    """
    import re

    problems = {}
    for path in FLOW_FILES:
        if path.parent.name != "flows":
            continue
        flat = re.sub(r"\s+", " ", path.read_text())
        hits = []
        for pat in (r"\.(choose|select_folder|save|open)\(\s*self\s*,",
                    r"transient_for\s*=\s*self\s*[,)]",
                    r"set_transient_for\(\s*self\s*\)"):
            hits += re.findall(pat, flat)
        if hits:
            problems[path.name] = hits
    assert not problems, (
        f"bare `self` used as dialog parent (use `self.win`): {problems}")
