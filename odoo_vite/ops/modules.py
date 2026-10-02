"""Module operations (PSS-5a): async core drivers for the UI facade.

Mirrors Qt's ModuleFlows + ModulesPage contracts (grouping rule
reimplemented — odoo_vite.ops must not import ui_qt, which pulls PySide6):

- refresh: list_modules + diff_modules → on_modules(iid, modules, diff, error)
  (Qt modulesReady parity; silent, no toast — the view paints error text).
- install → module_manager.install_modules (one-shot -i --stop-after-init).
- update → inline run_streaming -u (Qt _op parity: core has no single-module
  update fn; update_code is the 3-stage pipeline, a different flow).
- uninstall → module_manager.uninstall_modules (shell one-shot).
- update_code → module_manager.update_code (git+pip+-u; stopped-only).
- deps → get_dependency_graph edges split into (depends, required_by).
- run_tests → module_manager.run_module_tests (refuses the primary DB).

Long ops accept progress_cb + cancel and stream into the bridge-owned
ProgressDriver via the queue (UI thread appends; the worker only posts).
Passwords resolve inside core exactly like the Qt flows (no new threads
touch keyring beyond what Qt already does).

Divergences from Qt (documented choice):
- update's interpreter is effective_python() (adopted python_binary
  override honored); Qt hardcoded venv_path/bin/python.
- deps are computed from the edges core returns. Qt reads
  graph["depends"]/["required_by"], which core never returns — Qt's dialog
  always renders two empty lists.
"""

from __future__ import annotations

import asyncio

from odoo_vite.core import module_manager
from odoo_vite.core.instance import effective_python
from odoo_vite.core.proc import run_streaming
from odoo_vite.core.registry import get_instance
from odoo_vite.core.result import Result

STATE_FILTERS = ["All", "Installed", "Upgradeable", "Installable"]


def _ver_tuple(version_str: str) -> tuple:
    """Byte-parity copy of the Qt view helper (thin adapter: version
    comparison for the state filter; core/ owns the real logic)."""
    parts = []
    for chunk in str(version_str or "").replace("-", ".").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def state_category(mod: dict) -> str:
    """GTK _mod_state_category parity (pure, unit-tested against Qt)."""
    state = (mod.get("state") or "").strip()
    if state == "installed":
        installed = _ver_tuple(mod.get("installed_version") or "")
        available = _ver_tuple(mod.get("available_version") or "")
        latest = _ver_tuple(mod.get("latest_version") or "")
        if available and available > installed:
            return "Upgradeable"
        if latest and latest > installed:
            return "Upgradeable"
        return "Installed"
    if state in ("to install", "to upgrade"):
        return "Upgradeable"
    return "Installable"


def preview_command(inst, db_name: str, flag: str,
                    names: list[str]) -> list[str]:
    """Exact odoo-bin argv an op will run (Qt _confirm_command parity —
    the bridge shows this in the confirm dialog before spawning)."""
    return [effective_python(inst),
            f"{inst.community_path}/odoo-bin",
            "-c", inst.conf_path, "-d", db_name,
            flag, ",".join(names), "--stop-after-init"]


def split_deps(edges: list, name: str) -> tuple[list[str], list[str]]:
    """Edges ([module, depends_on]) → (depends-on, required-by), sorted."""
    depends = sorted(e[1] for e in edges if len(e) == 2 and e[0] == name
                     and e[1])
    required_by = sorted(e[0] for e in edges if len(e) == 2 and e[1] == name
                         and e[0])
    return depends, required_by


class ModuleOps:
    def __init__(self, on_message=None, on_refresh=None, on_modules=None,
                 on_deps=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._refresh = on_refresh or (lambda: None)
        self._modules = on_modules or (lambda _i, _m, _d, _e: None)
        self._deps = on_deps or (lambda _i, _n, _d, _r: None)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    def _instance_db(self, instance_id: str):
        """Resolve (instance, primary db) — Qt _instance_db parity."""
        inst = get_instance(instance_id)
        if inst is None:
            return None, ""
        db_name = (inst.primary_db or "").strip()
        return inst, db_name

    # ---------------------------------------------------------------- refresh

    async def refresh_modules(self, instance_id: str):
        """List + diff on the worker; results via the typed sink (silent —
        errors paint into the view, never toast)."""
        inst, db_name = self._instance_db(instance_id)
        if inst is None:
            self._modules(instance_id, [], {}, "Instance not found")
            return [], {}, "Instance not found"
        if not db_name:
            self._modules(instance_id, [], {},
                           "No primary database set — pick one first.")
            return [], {}, "No primary database set"

        def _work():
            mods = module_manager.list_modules(inst, db_name)
            diff = {}
            if mods.ok:
                dres = module_manager.diff_modules(inst, db_name)
                if dres.ok:
                    diff = {r["name"]: r
                            for r in dres.data.get("diff", [])}
            return mods, diff

        mods, diff = await asyncio.to_thread(_work)
        if mods.ok:
            modules = list(mods.data.get("modules", []))
            self._modules(instance_id, modules, diff, "")
            return modules, diff, ""
        self._modules(instance_id, [], {}, mods.message)
        return [], {}, mods.message

    # --------------------------------------------------------------------- ops

    async def install(self, instance_id: str, names: list,
                      progress_cb=None, cancel=None):
        inst, db_name = self._instance_db(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        if not db_name:
            self._message("No primary database set — pick one first.", "error")
            return Result.failure("No primary database set")
        mods = [n.strip() for n in (names or []) if n.strip()]
        if not mods:
            self._message("Select installable modules first", "error")
            return Result.failure("Select installable modules first")
        return await self._run(
            module_manager.install_modules, inst, db_name, mods,
            progress_cb=progress_cb, cancel=cancel)

    async def update(self, instance_id: str, names: list,
                     progress_cb=None, cancel=None):
        """Single-shot -u (Qt _op parity — core has no update_modules)."""
        inst, db_name = self._instance_db(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        if not db_name:
            self._message("No primary database set — pick one first.", "error")
            return Result.failure("No primary database set")
        mods = [n.strip() for n in (names or []) if n.strip()]
        if not mods:
            self._message("Select modules to update first", "error")
            return Result.failure("Select modules to update first")
        cmd = preview_command(inst, db_name, "-u", mods)

        def _work():
            res = run_streaming(cmd, progress_cb=progress_cb,
                                cancel=cancel, timeout=3600)
            if not res.ok:
                tail = "\n".join((res.data or {}).get("lines", [])[-10:])
                return Result.failure(
                    f"Update failed: {res.message}"
                    + (f"\n--- tail ---\n{tail}" if tail else ""))
            return Result.success(
                message=f"Updated {', '.join(mods)}")

        return await self._run(_work)

    async def uninstall(self, instance_id: str, name: str,
                        progress_cb=None, cancel=None):
        inst, db_name = self._instance_db(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        if not db_name:
            self._message("No primary database set — pick one first.", "error")
            return Result.failure("No primary database set")
        if not (name or "").strip():
            self._message("Pick a module first", "error")
            return Result.failure("Pick a module first")
        return await self._run(
            module_manager.uninstall_modules, inst, db_name,
            [(name or "").strip()], progress_cb=progress_cb, cancel=cancel)

    async def update_code(self, instance_id: str, progress_cb=None,
                          cancel=None):
        """3-stage pipeline over auto_update_modules (Qt guards parity)."""
        inst, _db = self._instance_db(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        if (inst.status or "") == "running":
            self._message("Stop the instance first — code changes under a "
                          "live server would half-apply", "error")
            return Result.failure("Stop the instance first — code changes "
                                  "under a live server would half-apply")
        mods = list(inst.auto_update_modules or [])
        if not mods:
            self._message("No auto-update modules configured — nothing to "
                          "update", "error")
            return Result.failure("No auto-update modules configured")
        return await self._run(
            module_manager.update_code, inst, mods,
            progress_cb=progress_cb, cancel=cancel)

    async def fetch_deps(self, instance_id: str, name: str):
        """Dependency panes for the viewer (silent errors → toast)."""
        inst, db_name = self._instance_db(instance_id)
        if inst is None or not db_name:
            self._message("Instance disappeared", "error")
            return [], []
        res = await asyncio.to_thread(
            module_manager.get_dependency_graph, inst, db_name)
        if not res.ok:
            self._message(res.message, "error")
            return [], []
        edges = (res.data or {}).get("edges", [])
        depends, required_by = split_deps(edges, name)
        self._deps(instance_id, name, depends, required_by)
        return depends, required_by

    async def run_tests(self, instance_id: str, db_name: str, module: str,
                        progress_cb=None, cancel=None):
        """One module's suite on a NON-primary DB (PSS-6 DevTools surfaces
        this; the op + guards live here with the other module ops)."""
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        return await self._run(
            module_manager.run_module_tests, inst, db_name, module,
            progress_cb=progress_cb, cancel=cancel)
