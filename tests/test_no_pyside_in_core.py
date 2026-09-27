"""Architectural guardrail (PSQ-1.3): core/ must import zero Qt modules.

Mirrors test_no_gtk_in_core.py — the migration must not leak PySide6/
shiboken6 into the framework-agnostic backend.
"""

import json
import subprocess
import sys

_GUI_PREFIXES = ("PySide6", "pyside6", "shiboken6", "shiboken")


def test_core_has_no_qt_imports():
    mods = [
        "odoo_vite.core.result",
        "odoo_vite.core.instance",
        "odoo_vite.core.registry",
        "odoo_vite.core.system_check",
        "odoo_vite.core.git_manager",
        "odoo_vite.core.events",
        "odoo_vite.core.proc",
        "odoo_vite.core.venv_manager",
        "odoo_vite.core.conf_writer",
        "odoo_vite.core.db_manager",
        "odoo_vite.core.process_manager",
        "odoo_vite.core.provisioning",
        "odoo_vite.core.audit",
        "odoo_vite.core.adopt",
        "odoo_vite.core.removal",
        "odoo_vite.core.settings",
        "odoo_vite.core.db_state",
        "odoo_vite.core.db_backup",
        "odoo_vite.core.module_manager",
        "odoo_vite.core.module_scaffolder",
        "odoo_vite.core.conf_manager",
        "odoo_vite.core.addon_paths",
        "odoo_vite.core.log_tail",
        "odoo_vite.core.log_search",
        "odoo_vite.core.log_doctor",
        "odoo_vite.core.profiler",
        "odoo_vite.core.odoo_rpc",
        "odoo_vite.core.odoo_inspect",
        "odoo_vite.core.devtools_export",
        "odoo_vite.core.odoo_shell",
        "odoo_vite.core.devwatch",
        "odoo_vite.core.backup_scheduler",
        "odoo_vite.core.clone",
        "odoo_vite.core.disk_usage",
        "odoo_vite.core.transfer",
        "odoo_vite.core.enterprise",
        # Not core/, but the headless systemd entry point — must stay
        # Qt-free for the same reason.
        "odoo_vite.backup_runner",
    ]
    # NOTE: this test deliberately does NOT delete/reimport modules
    # in-process (an earlier revision did, and it broke string-target
    # monkeypatching in later tests by splitting module identity).
    # Verification happens in a FRESH interpreter: zero Qt modules may
    # appear after importing every core module.
    probe = (
        "import sys, importlib, json; "
        f"mods = {mods!r}; "
        "bad = []\n"
        "for _m in mods:\n"
        "    importlib.import_module(_m)\n"
        "for _m in sys.modules:\n"
        "    if _m == 'PySide6' or _m.startswith("
        "('PySide6', 'pyside6', 'shiboken6', 'shiboken')):\n"
        "        bad.append(_m)\n"
        "print(json.dumps(sorted(bad)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    bad = json.loads(proc.stdout)
    assert not bad, f"core pulled in Qt modules: {bad}"
