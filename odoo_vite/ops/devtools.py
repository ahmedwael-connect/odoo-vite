"""DevTools operations (PSS-6b): async RPC drivers + record browser state.

Mirrors Qt's DevToolsRpcFlows contracts (odoo_vite.ops must not import ui_qt,
which pulls PySide6). Sessions + per-instance browser state live here,
exactly like the Qt flows object:

- connect (recall unpacked correctly), list_models, model_metadata (+
  first records page), rec_search/rec_page, rec_create/rec_update/
  rec_delete, cron_refresh, launch_json, open_editor.
- Shell sessions live in the BRIDGE (PTY ownership + UI-thread polling,
  Qt _shell_poll_tick parity) — only the record-test runner's progress
  plumbing is shared, via ModuleOps.run_tests + the bridge progress
  helper from PSS-5a.
- Dev Mode Watch is out of scope (Slint has no QFileSystemWatcher;
  needs a file-watch design of its own — noted in the plan).

Divergences from Qt (documented choice):
- recall_credentials returns a (user, password) TUPLE; Qt called
  .get("password") on it (AttributeError → recall silently never worked).
  Slint unpacks the tuple, so Remember actually remembers.
- delete_record's expected-name check happens in core; the ir.* structural
  refusal is kept here AND in core (belt and braces, Qt parity).
"""

from __future__ import annotations

import asyncio

from odoo_vite.core import devtools_export, odoo_inspect, odoo_rpc
from odoo_vite.core.registry import get_instance
from odoo_vite.core.result import Result

PAGE_SIZE = 50
OPERATORS = ["=", "!=", "like", "ilike", ">", "<", ">=", "<="]
RECORD_FIELDS = ["id", "display_name", "name"]
# Relational blobs are never edited (Qt record_dialog parity).
SKIP_FIELD_TYPES = ("many2one", "one2many", "many2many")


def diff_record_values(current: dict, new: dict) -> dict:
    """Changed fields {key: (old, new)} — Qt parity, stringified compare."""
    current = current or {}
    changed = {}
    for key, value in (new or {}).items():
        if str(current.get(key)) != str(value):
            changed[key] = (current.get(key), value)
    return changed


def editable_fields(meta: dict | None, record: dict | None) -> list[str]:
    """Field names for the record editor: metadata-driven when available
    (relational skipped), else the record's own keys (Qt parity).

    Qt checked spec["type"], but core specs carry "ttype" — so Qt's skip
    never fired and relationals landed in the form. Both keys honored.
    """
    fields = []
    for spec in ((meta or {}).get("fields") or []):
        if not isinstance(spec, dict):
            continue
        if spec.get("type", spec.get("ttype")) in SKIP_FIELD_TYPES:
            continue
        if spec.get("name"):
            fields.append(spec["name"])
    if not fields and record:
        fields = [k for k in record.keys() if k not in ("id", "__last_update")]
    return fields


def format_meta_line(meta: dict | None) -> str:
    """One-line model metadata for the inspector label."""
    fields = [s.get("name", "?") for s in ((meta or {}).get("fields") or [])
              if isinstance(s, dict)]
    if not fields:
        return "No fields loaded."
    shown = ", ".join(fields[:8])
    more = f" (+{len(fields) - 8} more)" if len(fields) > 8 else ""
    return f"{len(fields)} field(s): {shown}{more}"


def format_record_label(record: dict) -> str:
    """Single-line browser row: id + human name."""
    name = record.get("display_name") or record.get("name", "?")
    return f"#{record.get('id', '?')} — {name}"


def format_cron_line(cron: dict) -> str:
    """Qt set_crons parity."""
    return (f"{cron.get('name', '?')} — next: {cron.get('nextcall', '?')} "
            f"({'active' if cron.get('active') else 'paused'})")


class DevToolsOps:
    def __init__(self, on_message=None, on_rpc=None, on_models=None,
                 on_meta=None, on_records=None, on_crons=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._rpc = on_rpc or (lambda _i, _t: None)
        self._models = on_models or (lambda _i, _m: None)
        self._meta = on_meta or (lambda _i, _m: None)
        self._records = on_records or (lambda _i, _r, _o, _more: None)
        self._crons = on_crons or (lambda _i, _c: None)
        self._sessions: dict[str, dict] = {}
        self._browser: dict[str, dict] = {}
        self._last_meta: dict[str, dict] = {}

    # ---------------------------------------------------------------- connect

    def _session(self, instance_id: str):
        return self._sessions.get(instance_id)

    async def rpc_connect(self, instance_id: str, user: str = "",
                          password: str = "", remember: bool = False):
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        user = user or "admin"
        if not password:
            try:
                _saved_user, saved_pw = (
                    odoo_rpc.recall_credentials(instance_id) or ("", ""))
                password = saved_pw or ""
            except Exception:
                password = ""
        if not password:
            self._message("Enter the Odoo password first (never guessed, "
                          "never stored unless Remember is checked)",
                          "error")
            self._rpc(instance_id, "Not connected: no password.")
            return Result.failure("No password")
        self._message("Connecting…", "info")

        def _work():
            res = odoo_rpc.connect_instance(
                inst, db=None, odoo_user=user, odoo_password=password)
            if res.ok and remember:
                try:
                    odoo_rpc.remember_credentials(instance_id, user,
                                                  password)
                except Exception:
                    pass
            return res

        res = await asyncio.to_thread(_work)
        if res.ok:
            data = res.data or {}
            self._sessions[instance_id] = data
            self._browser.pop(instance_id, None)
            self._rpc(instance_id,
                       f"Connected as {data.get('user')} "
                       f"(db {data.get('db')}).")
            self._message("RPC connected", "info")
        else:
            self._rpc(instance_id, f"Not connected: {res.message}")
            self._message(res.message, "error")
        return res

    # ----------------------------------------------------------------- models

    async def list_models(self, instance_id: str):
        client = self._session(instance_id)
        if client is None:
            self._message("Connect RPC first", "error")
            return Result.failure("Connect RPC first")
        self._message("Listing models…", "info")
        res = await asyncio.to_thread(odoo_inspect.list_models, client)
        if res.ok:
            self._models(instance_id, (res.data or {}).get("models", []))
        self._message(res.message, "info" if res.ok else "error")
        return res

    async def model_metadata(self, instance_id: str, model: str):
        client = self._session(instance_id)
        if client is None or not model:
            return Result.failure("Connect RPC first")
        state = self._browser.setdefault(instance_id, {})
        state["model"] = model
        state["offset"] = 0
        self._message("Loading fields…", "info")
        res = await asyncio.to_thread(
            odoo_inspect.get_model_metadata, client, model)
        if res.ok:
            data = res.data or {}
            self._last_meta[instance_id] = data
            self._meta(instance_id, data)
        else:
            self._message(res.message, "error")
            return res
        return await self.records_page(instance_id, 0)

    # ---------------------------------------------------------------- records

    def _rec_state(self, instance_id: str) -> dict:
        return self._browser.setdefault(
            instance_id, {"model": "", "offset": 0, "cache": []})

    def last_meta(self, instance_id: str) -> dict:
        return self._last_meta.get(instance_id, {})

    def cached_record(self, instance_id: str, record_id: int):
        state = self._rec_state(instance_id)
        return next((r for r in state.get("cache", [])
                     if r.get("id") == record_id), None)

    async def records_page(self, instance_id: str, offset: int):
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            return Result.failure("Pick a model first")
        offset = max(0, offset)
        self._message("Loading records…", "info")

        def _work():
            res = odoo_inspect.search_records(
                client, model, domain=state.get("domain"),
                fields=list(RECORD_FIELDS), offset=offset, limit=PAGE_SIZE)
            if not res.ok:
                return res
            records = res.data if isinstance(res.data, list) else []
            return Result(ok=True, message=f"{len(records)} record(s)",
                          data={"records": records})

        res = await asyncio.to_thread(_work)
        if not res.ok:
            self._message(res.message, "error")
            return res
        records = (res.data or {}).get("records", [])
        state["offset"] = offset
        state["cache"] = records
        self._records(instance_id, records, offset,
                      len(records) >= PAGE_SIZE)
        return res

    async def rec_search(self, instance_id: str, field: str, op: str,
                         value: str):
        state = self._rec_state(instance_id)
        field, value = (field or "").strip(), (value or "").strip()
        state["domain"] = ([[field, op, value]] if field else None)
        return await self.records_page(instance_id, 0)

    async def rec_page(self, instance_id: str, delta: int):
        state = self._rec_state(instance_id)
        return await self.records_page(
            instance_id, state.get("offset", 0) + delta * PAGE_SIZE)

    async def rec_create(self, instance_id: str, values: dict):
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            self._message("Pick a model first", "error")
            return Result.failure("Pick a model first")
        self._message("Creating…", "info")
        res = await asyncio.to_thread(
            odoo_inspect.create_record, client, model, values)
        self._message(res.message, "info" if res.ok else "error")
        if res.ok:
            await self.records_page(instance_id, state.get("offset", 0))
        return res

    async def rec_update(self, instance_id: str, record_id: int,
                         values: dict):
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            self._message("Pick a model first", "error")
            return Result.failure("Pick a model first")
        self._message("Updating…", "info")
        res = await asyncio.to_thread(
            odoo_inspect.update_record, client, model, record_id, values)
        self._message(res.message, "info" if res.ok else "error")
        if res.ok:
            await self.records_page(instance_id, state.get("offset", 0))
        return res

    async def rec_delete(self, instance_id: str, record_id: int,
                         expected: str):
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            self._message("Pick a model first", "error")
            return Result.failure("Pick a model first")
        self._message("Deleting…", "info")
        res = await asyncio.to_thread(
            odoo_inspect.delete_record, client, model, record_id, expected)
        self._message(res.message, "info" if res.ok else "error")
        if res.ok:
            await self.records_page(instance_id, state.get("offset", 0))
        return res

    # ------------------------------------------------------------------- cron

    async def cron_refresh(self, instance_id: str):
        client = self._session(instance_id)
        if client is None:
            self._message("Connect RPC first", "error")
            return Result.failure("Connect RPC first")
        self._message("Loading cron jobs…", "info")
        res = await asyncio.to_thread(odoo_inspect.list_cron_jobs, client)
        if res.ok:
            data = res.data or {}
            self._crons(instance_id,
                        data.get("crons", data.get("jobs", [])))
        self._message(res.message, "info" if res.ok else "error")
        return res

    # ---------------------------------------------------------- launch/editors

    async def launch_json(self, instance_id: str):
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        res = await asyncio.to_thread(
            devtools_export.generate_launch_json, inst)
        self._message(res.message, "info" if res.ok else "error")
        return res

    async def open_editor(self, instance_id: str, editor: str):
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        folder = (inst.path or "").strip()
        if not folder:
            self._message("Instance records no path", "error")
            return Result.failure("Instance records no path")
        res = await asyncio.to_thread(
            devtools_export.open_in_editor, editor, folder)
        self._message(res.message, "info" if res.ok else "error")
        return res
