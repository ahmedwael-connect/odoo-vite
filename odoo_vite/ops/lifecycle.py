"""Lifecycle operations (PSS-3): async core drivers for the UI facade.

Every op runs the blocking core fn in asyncio.to_thread() and reports
back through UI-thread sinks — the direct translation of Qt's
run_in_background + flows, minus threads/forwarders/trackers:

- on_message(str): toast/status line (replaces flows' message signal).
- on_refresh(): re-read registry + repaint (replaces refreshRequested).

First-start confirm is non-blocking by core design (confirm_cb=None →
needs_confirm result): the bridge shows a ConfirmDialog and re-runs with
an already-confirmed callback. No worker ever touches widgets.
"""

from __future__ import annotations

import asyncio

from odoo_vite.core import clone as clone_core
from odoo_vite.core import process_manager, removal
from odoo_vite.core.registry import get_db_password, get_instance
from odoo_vite.core.result import Result


class LifecycleOps:
    def __init__(self, on_message=None, on_refresh=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._refresh = on_refresh or (lambda: None)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    async def start(self, instance_id: str, database=None,
                    confirm_cb=None):
        return await self._run(
            process_manager.start_instance, instance_id, database,
            confirm_cb)

    async def stop(self, instance_id: str):
        return await self._run(process_manager.stop_instance, instance_id)

    async def start_many(self, instance_ids: list[str], progress_cb=None,
                         cancel=None) -> Result:
        """Batch start — sequential in ONE worker (qt-architecture §5:
        parallel lifecycle workers would lose registry RMW updates)."""
        return await self._batch(
            "start", instance_ids, progress_cb, cancel)

    async def stop_many(self, instance_ids: list[str], progress_cb=None,
                        cancel=None) -> Result:
        """Batch stop — same single-worker rule as start_many."""
        return await self._batch("stop", instance_ids, progress_cb, cancel)

    async def _batch(self, action: str, instance_ids: list[str],
                     progress_cb=None, cancel=None) -> Result:
        """Run start/stop per id, sequentially. Running-state skips come
        from one live get_statuses() read (self-healing, before the loop);
        first-start DB confirmations are skipped — bulk cannot host
        per-instance preview dialogs, so they stay a single-instance flow.
        """
        ids = [str(i) for i in (instance_ids or []) if i]
        total = len(ids)
        by_id = {s["id"]: s for s in process_manager.get_statuses()}
        participle = "started" if action == "start" else "stopped"
        verb = "Starting" if action == "start" else "Stopping"
        done: list[str] = []
        skipped: list[str] = []
        confirms: list[str] = []
        failed: list[dict] = []
        left = 0

        for idx, iid in enumerate(ids, 1):
            if cancel is not None and cancel():
                left = total - idx + 1
                break
            entry = by_id.get(iid)
            if entry is None:
                failed.append({"name": iid, "reason": "not found"})
                if progress_cb is not None:
                    progress_cb(f"[{idx}/{total}] {iid}: not found")
                continue
            name = str(entry.get("name") or iid)
            running = str(entry.get("status") or "") == "running"
            if (action == "start" and running) or (
                    action == "stop" and not running):
                skipped.append(name)
                if progress_cb is not None:
                    label = "already running" if action == "start" \
                        else "not running"
                    progress_cb(f"[{idx}/{total}] {name}: skipped ({label})")
                continue
            if progress_cb is not None:
                progress_cb(f"[{idx}/{total}] {verb} {name}…")
            if action == "start":
                res = await asyncio.to_thread(
                    process_manager.start_instance, iid, None, None)
            else:
                res = await asyncio.to_thread(
                    process_manager.stop_instance, iid)
            if res.ok:
                done.append(name)
            elif isinstance(res.data, dict) and res.data.get("needs_confirm"):
                confirms.append(name)
            elif "is already running" in (res.message or ""):
                skipped.append(name)
            else:
                failed.append({"name": name, "reason": res.message})
            if progress_cb is not None:
                progress_cb(f"[{idx}/{total}] {name}: {res.message}")

        parts: list[str] = []
        if done:
            parts.append(f"{participle} {len(done)}")
        if skipped:
            label = "already running" if action == "start" else "not running"
            parts.append(f"skipped {len(skipped)} {label}")
        if confirms:
            parts.append(
                f"{len(confirms)} need database confirmation — "
                "start them individually")
        if failed:
            parts.append(f"{len(failed)} failed")
        if left:
            parts.append(f"cancelled ({left} remaining)")
        msg = "; ".join(parts) if parts else "nothing to do"
        for item in failed[:3]:
            msg += f" — {item['name']}: {item['reason']}"
        data = {"done": done, "skipped": skipped,
                "needs_confirm": confirms, "failed": failed}
        self._message(msg, "error" if failed else "info")
        self._refresh()
        return Result(ok=not failed, message=msg, data=data)

    async def restart(self, instance_id: str):
        return await self._run(
            process_manager.restart_instance, instance_id)

    async def remove(self, instance_id: str, drop_db: bool = False,
                     drop_extra_dbs: list | None = None):
        # 3.1.0 B1: pass by keyword — a 3rd positional would land in
        # remove_instance's db_path slot and silently drop extra DBs.
        return await self._run(
            removal.remove_instance, instance_id, drop_db,
            drop_extra_dbs=drop_extra_dbs or [])

    async def clone(self, instance_id: str, new_name: str,
                    new_port: int | None = None):
        """Password resolves HERE (UI thread) — Secret Service dbus calls
        from worker threads hang (Qt qt-architecture §5 rule, kept)."""
        inst = get_instance(instance_id)
        if inst is None:
            res = Result.failure(f"No instance with id '{instance_id}'")
            self._message(res.message, "info" if res.ok else "error")
            return res
        try:
            password = get_db_password(inst) or ""
        except Exception:
            password = ""
        return await self._run(
            clone_core.clone_instance, instance_id, new_name,
            new_port=new_port, src_password=password)
