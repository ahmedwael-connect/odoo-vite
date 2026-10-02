"""Database operations (PSS-4): async core drivers for the UI facade.

Mirrors Qt's DatabaseFlows + BackupSchedulesFlows contracts (grouping rule
reimplemented — odoo_vite.ops must not import ui_qt, which pulls PySide6):

- Passwords resolve on the caller (UI) thread; workers never touch the
  Secret Service dbus (Qt qt-architecture §5 rule, kept).
- Results go through sinks: on_message(str), on_refresh(), plus typed
  on_states(iid, states)/on_report(iid, report)/on_schedules(iid, ...).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from odoo_vite.core import backup_scheduler, db_backup, db_manager
from odoo_vite.core import process_manager
from odoo_vite.core.db_manager import track_database, untrack_database
from odoo_vite.core.db_state import (
    get_db_state,
    human_size,
    odoo_major,
    validate_db_config,
)
from odoo_vite.core.registry import (
    get_db_password,
    get_instance,
    update_instance,
)
from odoo_vite.core.result import Result


def group_discover_entries(entries: list, instance_version: str) -> dict:
    """Pure A.1 grouping: likely/other/plain. Only 'likely' pre-checks.

    likely = initialized AND major matches the instance version.
    """
    me = (instance_version or "").strip()
    groups = {"likely": [], "other": [], "plain": []}
    for entry in entries or []:
        name = entry.get("name", "")
        if entry.get("initialized") and entry.get("odoo_major"):
            key = "likely" if entry["odoo_major"] == me else "other"
        else:
            key = "plain"
        groups[key].append(name)
    for key in groups:
        groups[key] = sorted(groups[key])
    return groups


def pick_save_file(title: str, default: str, pattern: str) -> str:
    """Save picker via zenity (mockable seam; "" when unavailable)."""
    if shutil.which("zenity") is None:
        return ""
    try:
        proc = subprocess.run(
            ["zenity", "--file-selection", "--save", "--confirm-overwrite",
             f"--title={title}", f"--filename={default}",
             f"--file-filter={pattern}"],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def pick_open_file(title: str, pattern: str) -> str:
    """Open picker via zenity (mockable seam; "" when unavailable)."""
    if shutil.which("zenity") is None:
        return ""
    try:
        proc = subprocess.run(
            ["zenity", "--file-selection", f"--title={title}",
             f"--file-filter={pattern}"],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


class DatabaseOps:
    def __init__(self, on_message=None, on_refresh=None, on_states=None,
                 on_report=None, on_schedules=None, on_discover=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._refresh = on_refresh or (lambda: None)
        self._states = on_states or (lambda _i, _s: None)
        self._report = on_report or (lambda _i, _r: None)
        self._schedules = on_schedules or (lambda _i, _s, _t: None)
        self._discover = on_discover or (lambda _i, _p: None)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    def _instance_pw(self, instance_id: str):
        """Resolve (instance, password) on the caller thread."""
        inst = get_instance(instance_id)
        if inst is None:
            return None, None
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        return inst, pw

    # ---------------------------------------------------------------- states

    async def refresh_states(self, instance_id: str):
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance not found", "error")
            return {}

        def _work():
            states = {}
            for db_name in (inst.tracked_dbs or []):
                try:
                    st = get_db_state(db_name, inst.db_user, pw)
                    states[db_name] = {
                        "exists": st.exists, "initialized": st.initialized,
                        "odoo_version": st.odoo_version or "",
                        "size": human_size(st.size_bytes),
                        "size_bytes": st.size_bytes or 0,
                        "owner": st.owner or "",
                    }
                except Exception as exc:
                    states[db_name] = {"exists": False, "error": str(exc)}
            return Result(ok=True, message="", data={"states": states})

        res = await asyncio.to_thread(_work)
        self._states(instance_id, res.data.get("states", {}))
        return res.data.get("states", {})

    async def discover_entries(self, instance_id: str):
        """Untracked DBs with init/version probes (for the dialog)."""
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            payload = {"ok": False, "message": "Instance not found",
                       "entries": []}
            self._discover(instance_id, payload)
            return payload

        def _work():
            res = db_manager.list_databases_for_user(inst.db_user, pw)
            if not res.ok:
                return res
            tracked = set(inst.tracked_dbs or [])
            entries = []
            for name in (res.data or {}).get("databases", []):
                if name in tracked:
                    continue
                entry = {"name": name, "initialized": False,
                         "odoo_major": ""}
                try:
                    st = get_db_state(name, inst.db_user, pw)
                    entry["initialized"] = bool(st.initialized)
                    entry["odoo_major"] = odoo_major(st.odoo_version)
                except Exception:
                    pass
                entries.append(entry)
            return Result(ok=True,
                          message=f"{len(entries)} untracked found",
                          data={"entries": entries})

        res = await asyncio.to_thread(_work)
        if not res.ok:
            payload = {"ok": False, "message": res.message, "entries": []}
            self._discover(instance_id, payload)
            return payload
        payload = {"ok": True, "message": res.message,
                   "entries": res.data.get("entries", [])}
        self._discover(instance_id, payload)
        return payload

    # ------------------------------------------------------------------ ops

    async def init_db(self, instance_id: str, db_name: str):
        return await self._run(
            process_manager.initialize_database, instance_id, db_name)

    async def drop_db(self, instance_id: str, db_name: str):
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance not found", "error")
            return Result.failure("Instance not found")
        return await self._run(
            db_manager.drop_database, db_name, inst.db_user, pw)

    async def backup_db(self, instance_id: str, db_name: str, dest: str):
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance not found", "error")
            return Result.failure("Instance not found")
        return await self._run(
            db_backup.backup_database, db_name, dest,
            db_user=inst.db_user, db_password=pw,
            instance_id=inst.id, instance_name=inst.name)

    async def restore_db(self, instance_id: str, dump: str, target: str):
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance not found", "error")
            return Result.failure("Instance not found")
        return await self._run(
            db_backup.restore_database, dump, target,
            db_user=inst.db_user, db_password=pw)

    async def validate(self, instance_id: str):
        inst, _pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance not found", "error")
            return {}
        report = await asyncio.to_thread(validate_db_config, inst)
        self._report(instance_id, report)
        return report

    async def track(self, instance_id: str, db_name: str):
        return await self._run(track_database, instance_id, db_name)

    async def untrack(self, instance_id: str, db_name: str):
        return await self._run(untrack_database, instance_id, db_name)

    async def track_many(self, instance_id: str, db_names: list):
        """Batch sequentially — parallel tracks lose RMW updates (Qt rule)."""

        def _work():
            done, failed = [], []
            for name in db_names:
                res = track_database(instance_id, name)
                (done if res.ok else failed).append(name)
            if failed and not done:
                return Result.failure(
                    f"Could not track: {', '.join(failed)}")
            msg = f"Tracking {len(done)} database(s)"
            if failed:
                msg += f" ({len(failed)} failed: {', '.join(failed)})"
            return Result(ok=True, message=msg, data={"tracked": done})

        return await self._run(_work)

    async def set_primary(self, instance_id: str, db_name: str):
        return await self._run(
            update_instance, instance_id, None, **{"primary_db": db_name})

    # -------------------------------------------------------------- schedules

    async def refresh_schedules(self, instance_id: str):
        def _work():
            scheds = backup_scheduler.list_schedules(instance_id)
            try:
                status = backup_scheduler.timer_status()
                status_data = {"active": bool(status.get("active")),
                               "detail": str(status.get("detail", ""))}
            except Exception as exc:
                status_data = {"active": False, "detail": str(exc)}
            return Result(ok=True, message="",
                          data={"schedules": scheds, "status": status_data})

        res = await asyncio.to_thread(_work)
        self._schedules(instance_id, res.data.get("schedules", []),
                        res.data.get("status", {}))
        return res.data

    async def run_schedule_now(self, schedule_id: str):
        return await self._run(backup_scheduler.run_schedule, schedule_id)

    async def switch_db(self, instance_id: str, db_name: str,
                        confirm_cb=None):
        return await self._run(
            process_manager.switch_database, instance_id, db_name,
            confirm_cb)

    async def sched_create(self, instance_id: str, payload: dict):
        res = await self._run(
            backup_scheduler.create_schedule, instance_id,
            payload.get("databases", []), payload.get("cron", ""),
            retention_n=payload.get("retention_n", 7),
            retention_days=payload.get("retention_days", 0))
        if res.ok:
            await self._run(backup_scheduler.install_timer)
        return res

    async def sched_update(self, schedule_id: str, payload: dict):
        return await self._run(
            backup_scheduler.update_schedule, schedule_id,
            databases=payload.get("databases"),
            cron=payload.get("cron"),
            retention_n=payload.get("retention_n"),
            retention_days=payload.get("retention_days"))

    async def sched_delete(self, schedule_id: str):
        return await self._run(
            backup_scheduler.delete_schedule, schedule_id)

    async def sched_toggle(self, schedule_id: str, enabled: bool):
        return await self._run(
            backup_scheduler.set_enabled, schedule_id, enabled)

    async def file_delete(self, dump_path: str):
        return await self._run(
            backup_scheduler.delete_backup_file, dump_path)
