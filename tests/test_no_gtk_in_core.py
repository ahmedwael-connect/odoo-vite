"""Architectural guardrail (§1.1): core/ must import zero GTK modules."""

import importlib
import sys


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
    ]
    for name in mods:
        for loaded in list(sys.modules):
            if loaded == name or loaded.startswith(name + "."):
                del sys.modules[loaded]
    # Also purge GUI modules: keyring's backend discovery lazily imports gi
    # (libsecret backend) when store_db_password runs in earlier tests — that
    # residue must not be mistaken for a core/ top-level GTK import.
    for loaded in list(sys.modules):
        if loaded == "gi" or loaded.startswith(
                ("gi.", "gtk", "Gtk", "Adw", "adw")):
            del sys.modules[loaded]
    for name in mods:
        importlib.import_module(name)
    bad = [m for m in sys.modules if m == "gi" or m.startswith(("gi.", "gtk", "Gtk", "Adw", "adw"))]
    assert not bad, f"core pulled in GUI modules: {bad}"
