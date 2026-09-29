"""Architectural guardrails (PSS-1): frontend/backend separation, Slint era.

- core/ must import neither Qt NOR Slint (framework-agnostic backend).
- ui_slint/ must not import Qt (no PySide6/gi); ui_qt/ must not import
  slint. Both frontends share core/ untouched (migration-retro §4).
Verified in a FRESH interpreter like the older guardrails.
"""

import json
import subprocess
import sys


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


def test_slint_frontend_has_no_qt_imports():
    bad = _probe(["odoo_vite.ui_slint", "odoo_vite.ui_slint.bridge"],
                 ("PySide6", "pyside6", "shiboken6", "shiboken", "gi"))
    assert not bad, f"ui_slint pulled in Qt modules: {bad}"
