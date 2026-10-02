"""Web-cutover guardrails: layering rules for ui_web/.

- core/ and the headless facade must never import pywebview (the window
  bootstrap is the only place allowed to — Phase 4 keeps it out of the
  probed modules).
- ui_web sources (except window.py) must not import Slint
  directly — the facade reaches UI logic only through the framework-free
  ops classes.

Verified in a FRESH interpreter like the sibling guardrails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

UI_WEB = Path(__file__).resolve().parent.parent / "odoo_vite" / "ui_web"

# window.py (Phase 4) is permitted to import webview; these must not.
_HEADLESS_MODULES = [
    "odoo_vite.core.result",
    "odoo_vite.core.instance",
    "odoo_vite.core.registry",
    "odoo_vite.core.system_check",
    "odoo_vite.core.provisioning",
    "odoo_vite.core.process_manager",
    "odoo_vite.core.clone",
    "odoo_vite.core.transfer",
    "odoo_vite.core.backup_scheduler",
    "odoo_vite.ui_web",
    "odoo_vite.ui_web.api",
    "odoo_vite.ui_web.push",
]

_HEADLESS_SOURCES = (
    "__init__.py",
    "api.py",
    "push.py",
    "serialize.py",
)


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


def test_core_and_facade_have_no_pywebview_imports():
    bad = _probe(_HEADLESS_MODULES, ("webview",))
    assert not bad, f"core/facade pulled in pywebview: {bad}"


def test_ui_web_sources_do_not_import_slint_directly():
    pattern = re.compile(r"^\s*(?:import slint\b|from slint\b)", re.M)
    for name in _HEADLESS_SOURCES:
        text = (UI_WEB / name).read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, f"{name} imports Slint directly: {match and match.group(0)}"
