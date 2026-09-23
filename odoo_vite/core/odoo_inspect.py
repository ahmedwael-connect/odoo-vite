"""Dev-tools domain operations over odoo_rpc (Sprint 10: 10.2–10.4).

All calls route through core/odoo_rpc.py (the single client) — nothing here
touches xmlrpc directly. Realistic scope per spec: fields from
ir.model.fields, constraints (SQL + Python) from ir.model.constraint,
access rules from ir.model.access. Python *method* signatures are NOT
introspectable over RPC for arbitrary methods — not attempted.

Record CRUD takes an explicit client dict {url, db, uid, password} as
returned by odoo_rpc.connect_instance. No GTK imports.
"""

from __future__ import annotations

from odoo_vite.core import odoo_rpc
from odoo_vite.core.result import Result

MODEL_FIELDS = ["name", "model", "field_description", "ttype", "relation",
                "required", "readonly", "compute", "store", "help"]


def list_models(client: dict) -> Result:
    """All models: [{name (technical), display, info}]."""
    res = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.model", "search_read", [[]],
        {"fields": ["name", "model", "info"], "order": "model", "limit": 2000})
    if not res.ok:
        return Result.failure(f"Cannot list models: {res.message}")
    out = [{"technical": m.get("model", ""), "display": m.get("name", ""),
            "info": m.get("info", "")} for m in (res.data or [])]
    return Result.success(data={"models": out},
                          message=f"{len(out)} model(s)")


def get_model_metadata(client: dict, model_name: str) -> Result:
    """Fields + constraints + access rules for one model."""
    model_name = (model_name or "").strip()
    if not model_name:
        return Result.failure("Model name is required")

    fields = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.model.fields", "search_read",
        [[["model", "=", model_name]]],
        {"fields": ["name", "field_description", "ttype", "relation",
                    "required", "readonly", "compute", "store", "help"],
         "order": "name", "limit": 2000})
    if not fields.ok:
        return Result.failure(f"Cannot read fields of '{model_name}': {fields.message}")
    constraints = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.model.constraint", "search_read",
        [[["model", "=", model_name]]],
        {"fields": ["name", "definition", "type", "message"], "limit": 500})
    access = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.model.access", "search_read",
        [[["model_id.model", "=", model_name]]],
        {"fields": ["name", "group_id", "perm_read", "perm_write",
                    "perm_create", "perm_unlink"], "limit": 500})
    for label, res in (("constraints", constraints), ("access rules", access)):
        if not res.ok:
            return Result.failure(f"Cannot read {label} of '{model_name}': {res.message}")
    return Result.success(data={
        "model": model_name,
        "fields": fields.data or [],
        "constraints": constraints.data or [],
        "access": access.data or [],
    }, message=f"Metadata for '{model_name}'")


def list_cron_jobs(client: dict) -> Result:
    """Cron jobs across Odoo versions (field discovery, not assumptions).

    15.0/16.0 keep name/model_id/function/args ON ir.cron; 17.0+ moved the
    callable to a linked ir.actions.server record (cron_name + server id
    instead). fields_get() tells us which shape we're talking to.
    """
    fg = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.cron", "fields_get", [], {})
    if not fg.ok or not isinstance(fg.data, dict):
        return Result.failure(f"Cannot inspect ir.cron fields: {fg.message}")
    available = set(fg.data)
    want = ["cron_name", "name", "active", "nextcall", "interval_number",
            "interval_type", "model_id", "function", "args",
            "ir_actions_server_id"]
    fields = [f for f in want if f in available]
    res = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        "ir.cron", "search_read", [[]],
        {"fields": fields, "order": "id", "limit": 1000})
    if not res.ok:
        return Result.failure(f"Cannot list cron jobs: {res.message}")

    server_ids = set()
    for job in res.data or []:
        ref = job.get("ir_actions_server_id")
        if isinstance(ref, (list, tuple)) and ref:
            try:
                server_ids.add(int(ref[0]))
            except (TypeError, ValueError):
                pass
    servers: dict = {}
    if server_ids:
        sres = odoo_rpc.execute_kw(
            client["url"], client["db"], client["uid"], client["password"],
            "ir.actions.server", "search_read",
            [[["id", "in", sorted(server_ids)]]],
            {"fields": ["model_id", "state", "code"], "limit": 1000})
        if sres.ok:
            for srv in sres.data or []:
                servers[srv.get("id")] = srv

    def _m2o(value):
        if isinstance(value, (list, tuple)) and value:
            return value[1] if len(value) > 1 else str(value[0])
        return str(value or "")

    out = []
    for job in res.data or []:
        model, function = "", ""
        direct_model = job.get("model_id")
        if direct_model:
            model = _m2o(direct_model)
            function = str(job.get("function") or "")
        else:
            ref = job.get("ir_actions_server_id")
            sid = None
            if isinstance(ref, (list, tuple)) and ref:
                try:
                    sid = int(ref[0])
                except (TypeError, ValueError):
                    sid = None
            srv = servers.get(sid, {})
            model = _m2o(srv.get("model_id")) or _m2o(srv.get("model_name"))
            code = str(srv.get("code") or "").strip().splitlines()
            function = (srv.get("state") or "")
            if code:
                function = f"{function}: {code[0][:80]}" if function else code[0][:80]
        out.append({
            "id": job.get("id"),
            "name": job.get("cron_name") or job.get("name") or "",
            "active": bool(job.get("active")),
            "nextcall": str(job.get("nextcall") or ""),
            "interval": f"{job.get('interval_number')} {job.get('interval_type')}",
            "model": model,
            "function": function,
        })
    return Result.success(data={"crons": out}, message=f"{len(out)} cron job(s)")


# ------------------------------------------------------- record CRUD (10.3)
def search_records(client: dict, model: str, domain=None, fields=None,
                   offset: int = 0, limit: int = 50, order: str = "") -> Result:
    if not (model or "").strip():
        return Result.failure("Model is required")
    return odoo_rpc.search_read(
        client["url"], client["db"], client["uid"], client["password"],
        model.strip(), domain=domain, fields=fields,
        offset=offset, limit=limit, order=order)


def create_record(client: dict, model: str, values: dict) -> Result:
    if not (model or "").strip():
        return Result.failure("Model is required")
    if not isinstance(values, dict) or not values:
        return Result.failure("Provide at least one field value")
    res = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        model.strip(), "create", [values])
    if not res.ok:
        return Result.failure(f"Create failed: {res.message}")
    return Result.success(data={"id": res.data},
                          message=f"Created {model} #{res.data}")


def update_record(client: dict, model: str, record_id: int,
                  values: dict) -> Result:
    """Write with an explicit changed-fields preview computed by the CALLER
    (the UI shows current-vs-new before calling)."""
    if not (model or "").strip():
        return Result.failure("Model is required")
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return Result.failure(f"Invalid record id '{record_id}'")
    if not isinstance(values, dict) or not values:
        return Result.failure("Nothing to update")
    res = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        model.strip(), "write", [[record_id], values])
    if not res.ok:
        return Result.failure(f"Update failed: {res.message}")
    return Result.success(data={"id": record_id, "updated": values},
                          message=f"Updated {model} #{record_id}")


def delete_record(client: dict, model: str, record_id: int,
                  display_name: str = "") -> Result:
    """Unlink one record. The UI type-to-confirms display_name first —
    this function just executes (and re-verifies existence first)."""
    if not (model or "").strip():
        return Result.failure("Model is required")
    try:
        record_id = int(record_id)
    except (TypeError, ValueError):
        return Result.failure(f"Invalid record id '{record_id}'")
    exists = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        model.strip(), "exists", [[record_id]])
    if not exists.ok:
        return Result.failure(f"Cannot verify record: {exists.message}")
    if not exists.data:
        return Result.failure(
            f"{model} #{record_id} does not exist (already deleted?)")
    res = odoo_rpc.execute_kw(
        client["url"], client["db"], client["uid"], client["password"],
        model.strip(), "unlink", [[record_id]])
    if not res.ok:
        return Result.failure(
            f"Delete failed (Odoo may refuse on dependents): {res.message}")
    label = display_name or f"{model} #{record_id}"
    return Result.success(data={"id": record_id},
                          message=f"Deleted {label}")
