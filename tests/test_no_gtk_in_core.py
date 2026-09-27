"""Architectural guardrail (§1.1): core/ must import zero GTK modules.

Verified in a FRESH interpreter (PSQ-1 lesson): in-process sys.modules
deletion splits module identity and breaks string-target monkeypatching
in later tests; snapshot-diffs are blind to pre-imported GUI modules.
The fresh interpreter also makes the old keyring/gi-residue purge
unnecessary — nothing triggers backend discovery on bare core imports.
"""

import json
import subprocess
import sys
from pathlib import Path


def test_core_has_no_gtk_imports():
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
    ]
    probe = (
        "import sys, importlib, json; "
        f"mods = {mods!r}; "
        "bad = []\n"
        "for _m in mods:\n"
        "    importlib.import_module(_m)\n"
        "for _m in sys.modules:\n"
        "    if _m == 'gi' or _m.startswith("
        "('gi.', 'gtk', 'Gtk', 'Adw', 'adw')):\n"
        "        bad.append(_m)\n"
        "print(json.dumps(sorted(bad)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    bad = json.loads(proc.stdout)
    assert not bad, f"core pulled in GUI modules: {bad}"


def test_events_has_no_gi_source():
    """U1.1: events.py must not reference gi/GLib even lazily (Qt uses
    queued signals in ui_qt/workers.py instead)."""
    src = (Path(__file__).resolve().parent.parent
           / "odoo_vite" / "core" / "events.py").read_text()
    for token in ("from gi", "import gi", "GLib", "idle_add"):
        assert token not in src, f"events.py still mentions {token!r}"
