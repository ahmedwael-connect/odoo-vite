"""Configuration operations (PSS-5b): conf + metadata drivers.

Mirrors Qt's ConfigurationPage/ConfigurationFlows contracts (odoo_vite.ops must
not import ui_qt, which pulls PySide6):

- Display reads are local and instant (Qt refresh_conf parity): the pure
  read_conf_view() builds everything the view needs; the bridge calls it
  on the UI thread, no workers involved.
- Writes go async through ConfigOps (message + refresh sinks, like the
  other ops classes): save / restore / regenerate / meta-save /
  apply-addons.
- meta_save keeps the Qt ordering: workers pre-check BEFORE any write,
  registry first, conf keys second — and validate_meta() (python-binary
  executable check) runs on the bridge before spawning.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

from odoo_vite.core import addon_paths, conf_manager
from odoo_vite.core.registry import get_instance, update_instance
from odoo_vite.core.result import Result

COMMON_KEYS = ["db_host", "db_port", "db_user", "xmlrpc_port", "logfile"]
LOG_LEVELS = ["info", "debug", "debug_sql", "warning", "error", "critical"]


def pick_open_dir(title: str) -> str:
    """Folder picker via zenity (mockable seam; "" when unavailable)."""
    if shutil.which("zenity") is None:
        return ""
    try:
        proc = subprocess.run(
            ["zenity", "--file-selection", "--directory",
             f"--title={title}"],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_conf_view(inst) -> dict:
    """Pure display model for one instance (Qt refresh_conf parity).

    Returns conf_path/options/paired-lists/common/addons/backup/meta or
    {"error": ...} when nothing readable is there. Never raises.
    """
    if inst is None or not inst.conf_path:
        return {"error": "No conf recorded."}
    res = conf_manager.read_conf(inst.conf_path)
    if not res.ok:
        return {"error": f"Cannot read conf: {res.message}"}
    options = dict(res.data["options"])
    info = conf_manager.conf_backup_info(inst.conf_path)
    return {
        "error": "",
        "conf_path": inst.conf_path,
        "options": options,
        "lines": [f"{k} = {v}" for k, v in options.items()],
        "common": {k: options.get(k, "") for k in COMMON_KEYS},
        "addons_path": options.get("addons_path", "—"),
        "backup_path": info["path"] if info else "",
        "description": inst.description or "",
        "workers": inst.workers or 0,
        "log_level": inst.log_level or "info",
        "python_binary": inst.python_binary or "",
        # 3.2.0 F1: both update lists drive the metadata card editor.
        "auto_update_modules": list(
            getattr(inst, "auto_update_modules", None) or []),
        "pending_update_modules": list(
            getattr(inst, "pending_update_modules", None) or []),
    }


def validate_meta(meta: dict) -> str | None:
    """Qt _on_meta_save parity: custom interpreters must be executable.
    Returns the error text, or None when the metadata may be saved."""
    pybin = str(meta.get("python_binary", "") or "").strip()
    if pybin and not (os.path.isfile(pybin) and os.access(pybin, os.X_OK)):
        return f"Not an executable: {pybin}"
    return None


class ConfigOps:
    def __init__(self, on_message=None, on_refresh=None) -> None:
        self._message = on_message or (lambda _m, _k="info": None)
        self._refresh = on_refresh or (lambda: None)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        if res.ok:
            # Every conf write takes effect on next restart (core rule —
            # the UI carries the note so no edit looks live). Registry
            # metadata applies immediately; the conf half doesn't.
            res = Result.success(
                data=res.data,
                message=res.message + " (takes effect on next restart)")
        # 3.2.0 P1: failures were pushed as level "info" and rendered like
        # a success toast — failures belong on the error channel.
        self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    async def save(self, instance_id: str, changes: dict):
        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            return conf_manager.update_conf_keys(inst.conf_path,
                                                 changes or {})
        return await self._run(_work)

    async def restore(self, instance_id: str):
        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            return conf_manager.restore_conf_backup(inst.conf_path)
        return await self._run(_work)

    async def regenerate(self, instance_id: str):
        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            return conf_manager.regenerate_conf(inst)
        return await self._run(_work)

    async def meta_save(self, instance_id: str, meta: dict):
        """Registry + conf metadata write (Qt meta_save parity: workers
        pre-check runs before ANY write).

        3.2.0 F1: keys absent from `meta` are left untouched, so callers
        may write a subset (e.g. only the pending-update queue from the
        Overview) without wiping description/workers/log_level/python.
        """

        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")

            fields: dict = {}
            workers: int | None = None
            if "workers" in meta:
                try:
                    workers = int(meta.get("workers", 0) or 0)
                except (TypeError, ValueError):
                    return Result.failure(
                        f"Invalid workers value '{meta.get('workers')}'")
                pre = conf_manager.check_workers_prereqs(workers,
                                                         inst.conf_path)
                if not pre.ok:
                    return Result.failure(pre.message)
                fields["workers"] = workers
            if "description" in meta:
                fields["description"] = str(meta.get("description") or "")
            if "log_level" in meta:
                fields["log_level"] = meta.get("log_level") or "info"
            if "python_binary" in meta:
                fields["python_binary"] = str(meta.get("python_binary") or "")
            for key in ("auto_update_modules", "pending_update_modules"):
                if key in meta:
                    raw = meta.get(key)
                    if not isinstance(raw, (list, tuple)):
                        return Result.failure(
                            f"'{key}' must be a list of module names")
                    fields[key] = [
                        str(m).strip() for m in raw if str(m).strip()]

            if fields:
                res = update_instance(instance_id, **fields)
                if not res.ok:
                    return Result.failure(res.message)

            conf_fields: dict = {}
            if workers is not None:
                conf_fields["workers"] = str(workers)
            if "log_level" in meta:
                conf_fields["log_level"] = meta.get("log_level") or "info"
            if conf_fields:
                conf_res = conf_manager.update_conf_keys(inst.conf_path,
                                                         conf_fields)
                if not conf_res.ok:
                    return Result.failure(
                        "Metadata saved, but conf write failed: "
                        + conf_res.message)

            if not fields and not conf_fields:
                return Result.success(message="Nothing to save")
            parts = []
            if fields:
                parts.append("registry")
            if conf_fields:
                parts.append("odoo.conf")
            return Result.success(
                message="Metadata saved (" + " + ".join(parts) + ")")

        return await self._run(_work)

    async def apply_addons(self, instance_id: str, entries: list):
        return await self._run(
            addon_paths.apply_addons_state, instance_id, entries)
