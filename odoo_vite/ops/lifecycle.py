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

    async def restart(self, instance_id: str):
        return await self._run(
            process_manager.restart_instance, instance_id)

    async def remove(self, instance_id: str, drop_db: bool = False,
                     drop_extra_dbs: list | None = None):
        return await self._run(
            removal.remove_instance, instance_id, drop_db,
            drop_extra_dbs or [])

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
