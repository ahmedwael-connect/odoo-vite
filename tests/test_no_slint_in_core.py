"""Architectural guardrails: frontend/backend separation (web era).

- core/ must import neither Qt nor Slint nor pywebview (framework-
  agnostic backend; siblings: test_no_gtk_in_core.py,
  test_no_webview_in_core.py).
- odoo_vite/ops/ (the framework-free ops layer both frontends shared)
  must import neither Slint nor Qt — the pywebview facade wraps it.
- odoo_vite/ui_web/ headless sources must not import Slint directly.

Verified in a FRESH interpreter like the older guardrails.
"""

import json
import subprocess
import sys

_GUI_PREFIXES = ("slint", "PySide6", "pyside6", "shiboken6", "shiboken", "gi")

_OPS_MODULES = [
    "odoo_vite.ops",
    "odoo_vite.ops.configuration",
    "odoo_vite.ops.databases",
    "odoo_vite.ops.devtools",
    "odoo_vite.ops.lifecycle",
    "odoo_vite.ops.logs",
    "odoo_vite.ops.modules",
    "odoo_vite.ops.transfer",
    "odoo_vite.ops.wizards",
]

_UI_WEB_HEADLESS = [
    "odoo_vite.ui_web",
    "odoo_vite.ui_web.api",
    "odoo_vite.ui_web.push",
    "odoo_vite.ui_web.serialize",
]


def _probe(mods: list[str], prefixes: tuple[str, ...]) -> list[str]:
    probe = (
        "import sys, importlib, json; "
        f"mods = {mods!r}; "
        "bad = []\n"
        "for _m in mods:\n"
        "    importlib.import_module(_m)\n"
        "for _m in sys.modules:\n"
        f"    if _m.split('.')[0] in {prefixes!r}:\n"
        "        bad.append(_m)\n"
        "print(json.dumps(sorted(bad)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


def test_core_has_no_slint_imports():
    mods = [
        "odoo_vite.core.result",
        "odoo_vite.core.instance",
        "odoo_vite.core.registry",
        "odoo_vite.core.system_check",
        "odoo_vite.core.provisioning",
        "odoo_vite.core.process_manager",
        "odoo_vite.core.clone",
        "odoo_vite.core.transfer",
        "odoo_vite.core.backup_scheduler",
    ]
    bad = _probe(mods, ("slint",))
    assert not bad, f"core pulled in Slint modules: {bad}"


def test_ops_has_no_gui_imports():
    bad = _probe(_OPS_MODULES, _GUI_PREFIXES)
    assert not bad, f"ops layer pulled in Slint/Qt modules: {bad}"


def test_ui_web_headless_has_no_gui_imports():
    # window.py is excluded: it is the one module allowed to import
    # pywebview (its GTK backend may pull gi at start time).
    bad = _probe(_UI_WEB_HEADLESS, _GUI_PREFIXES)
    assert not bad, f"ui_web headless pulled in Slint/Qt modules: {bad}"
