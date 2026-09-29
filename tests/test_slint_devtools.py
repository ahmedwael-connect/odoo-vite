"""PSS-6b: devtools ops with STUBBED RPC core (no Odoo, no PG).

Slint-free by construction (imports core + ui_slint.devtools only): per
docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. asyncio.run() drives the coroutines; odoo_inspect /
odoo_rpc are stubbed at the boundary (their shape is core-tested).
"""

import asyncio

import pytest

pytest.importorskip("slint", reason="slint package required")

from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ui_slint.devtools import (  # noqa: E402
    DevToolsOps,
    diff_record_values,
    editable_fields,
    format_cron_line,
    format_meta_line,
    format_record_label,
)


def _ops():
    messages = []
    sinks = {"rpc": [], "models": [], "meta": [], "records": [],
             "crons": []}
    ops = DevToolsOps(
        lambda m, k="info": messages.append((m, k)),
        lambda i, t: sinks["rpc"].append((i, t)),
        lambda i, m: sinks["models"].append((i, m)),
        lambda i, m: sinks["meta"].append((i, m)),
        lambda i, r, o, more: sinks["records"].append((i, r, o, more)),
        lambda i, c: sinks["crons"].append((i, c)))
    return ops, messages, sinks


def test_diff_record_values_matches_qt():
    """Diff rule, frozen at cutover (was byte-parity-tested against the
    Qt flow until ui_qt/ was deleted in PSS-9)."""
    current = {"name": "Desk", "price": 10, "active": True}
    new = {"name": "Desk", "price": "10", "active": False, "extra": "x"}
    assert diff_record_values(current, new) == {
        "active": (True, False), "extra": (None, "x")}
    assert diff_record_values(current, dict(current)) == {}
    assert diff_record_values({}, {"a": 1}) == {"a": (None, 1)}


def test_editable_fields_skips_relational():
    """Qt checked spec['type']; core specs carry 'ttype' — both honored."""
    meta = {"fields": [
        {"name": "name", "type": "char"},
        {"name": "partner_id", "ttype": "many2one"},
        {"name": "order_ids", "type": "one2many"},
        {"name": "price", "ttype": "float"},
        {"name": "", "type": "char"},
        "junk",
    ]}
    assert editable_fields(meta, None) == ["name", "price"]
    record = {"id": 1, "name": "Desk", "__last_update": "x"}
    assert editable_fields({}, record) == ["name"]
    assert editable_fields(None, None) == []


def test_format_helpers():
    assert format_record_label(
        {"id": 3, "display_name": "Desk"}) == "#3 — Desk"
    assert format_record_label({"id": 4, "name": "Chair"}) == "#4 — Chair"
    assert format_cron_line(
        {"name": "Recompute", "nextcall": "tomorrow",
         "active": True}) == "Recompute — next: tomorrow (active)"
    assert format_cron_line({}) == "? — next: ? (paused)"
    meta = {"fields": [{"name": f"f{i}"} for i in range(10)]}
    line = format_meta_line(meta)
    assert line.startswith("10 field(s): ") and "(+2 more)" in line
    assert format_meta_line({}) == "No fields loaded."


def test_unknown_ids_fail():
    ops, messages, sinks = _ops()
    assert not asyncio.run(
        ops.rpc_connect("no-such-id", "admin", "pw")).ok
    assert not asyncio.run(ops.list_models("no-such-id")).ok
    assert not asyncio.run(ops.cron_refresh("no-such-id")).ok
    assert not asyncio.run(ops.launch_json("no-such-id")).ok
    assert not asyncio.run(ops.open_editor("no-such-id", "code")).ok
    assert all(v == [] for v in sinks.values())
    assert messages


def test_rpc_connect_needs_password(tmp_path, monkeypatch):
    from odoo_vite.core import odoo_rpc
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "dev.db"))
    inst = Instance(name="DT", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    monkeypatch.setattr(odoo_rpc, "recall_credentials",
                        lambda _i: ("", ""))
    ops, messages, sinks = _ops()
    res = asyncio.run(ops.rpc_connect(inst.id, "admin", ""))
    assert not res.ok
    assert sinks["rpc"] == [(inst.id, "Not connected: no password.")]
    assert any("password" in m for m, _k in messages)


def test_rpc_connect_round_trip(tmp_path, monkeypatch):
    from odoo_vite.core import odoo_rpc
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "dev2.db"))
    inst = Instance(name="DT2", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    session = {"url": "http://x", "db": "main", "uid": 2,
               "user": "admin", "password": "pw"}
    monkeypatch.setattr(
        odoo_rpc, "connect_instance",
        lambda *a, **k: Result(ok=True, message="ok", data=dict(session)))
    ops, _, sinks = _ops()
    res = asyncio.run(ops.rpc_connect(inst.id, "admin", "pw"))
    assert res.ok
    assert sinks["rpc"] == [(inst.id, "Connected as admin (db main).")]
    assert ops._session(inst.id) == session


def _connected_ops(tmp_path, monkeypatch, name="DTB"):
    """Ops with a fake session (no network): stub inspect boundary."""
    from odoo_vite.core import odoo_inspect, odoo_rpc
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / f"{name}.db"))
    inst = Instance(name=name, version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    ops, messages, sinks = _ops()
    ops._sessions[inst.id] = {"url": "u", "db": "main", "uid": 2,
                              "user": "admin", "password": "pw"}
    pages = [
        [{"id": 1, "display_name": "Desk"},
         {"id": 2, "display_name": "Chair"}],
        [{"id": 3, "display_name": "Table"}],
    ]
    monkeypatch.setattr(
        odoo_inspect, "list_models",
        lambda _c: Result(ok=True, message="2",
                          data={"models": [
                              {"technical": "sale.order",
                               "display": "Sales Order"}]}))
    monkeypatch.setattr(
        odoo_inspect, "get_model_metadata",
        lambda _c, _m: Result(ok=True, message="meta",
                              data={"fields": [
                                  {"name": "name", "ttype": "char"},
                                  {"name": "partner_id",
                                   "ttype": "many2one"}]}))

    def _search(_c, _m, domain=None, fields=None, offset=0, limit=50):
        assert fields == ["id", "display_name", "name"]
        return Result(ok=True, message="page",
                      data=list(pages[0] if offset == 0 else pages[1]))

    monkeypatch.setattr(odoo_inspect, "search_records", _search)
    monkeypatch.setattr(
        odoo_rpc, "connect_instance",
        lambda *a, **k: Result(ok=False, message="no net"))
    return ops, messages, sinks, inst


def test_browser_flow(tmp_path, monkeypatch):
    ops, _, sinks, inst = _connected_ops(tmp_path, monkeypatch)
    res = asyncio.run(ops.list_models(inst.id))
    assert res.ok
    assert sinks["models"][0][1][0]["technical"] == "sale.order"
    res = asyncio.run(ops.model_metadata(inst.id, "sale.order"))
    assert res.ok  # metadata + first records page
    assert sinks["meta"][0][0] == inst.id
    assert "name" in str(sinks["meta"][0][1])
    iid, records, offset, more = sinks["records"][0]
    assert (iid, offset, more) == (inst.id, 0, False)
    assert [r["id"] for r in records] == [1, 2]
    # Domain search resets to page 0.
    asyncio.run(ops.rec_search(inst.id, "name", "like", "Desk"))
    assert ops._browser[inst.id]["domain"] == [["name", "like", "Desk"]]
    assert sinks["records"][-1][2] == 0
    # Paging forward clamps at zero on the way back.
    asyncio.run(ops.rec_page(inst.id, 1))
    assert sinks["records"][-1][2] == 50
    asyncio.run(ops.rec_page(inst.id, -1))
    assert sinks["records"][-1][2] == 0
    # Cache + meta accessors for the bridge flows.
    assert ops.cached_record(inst.id, 1)["display_name"] == "Desk"
    assert ops.cached_record(inst.id, 99) is None
    assert [s["name"] for s in ops.last_meta(inst.id)["fields"]] == \
        ["name", "partner_id"]


def test_record_cud_refresh_page(tmp_path, monkeypatch):
    from odoo_vite.core import odoo_inspect

    ops, _, sinks, inst = _connected_ops(tmp_path, monkeypatch,
                                         name="DTC")
    asyncio.run(ops.model_metadata(inst.id, "sale.order"))
    seen = {}
    monkeypatch.setattr(
        odoo_inspect, "create_record",
        lambda *a, **k: seen.update(op="create") or Result.success(
            message="Created"))
    monkeypatch.setattr(
        odoo_inspect, "update_record",
        lambda *a, **k: seen.update(op="update") or Result.success(
            message="Updated"))
    monkeypatch.setattr(
        odoo_inspect, "delete_record",
        lambda *a, **k: seen.update(op="delete") or Result.success(
            message="Deleted"))
    n = len(sinks["records"])
    assert asyncio.run(ops.rec_create(inst.id, {"name": "X"})).ok
    assert seen["op"] == "create" and len(sinks["records"]) == n + 1
    assert asyncio.run(ops.rec_update(inst.id, 1, {"name": "Y"})).ok
    assert seen["op"] == "update"
    assert asyncio.run(
        ops.rec_delete(inst.id, 1, "Desk")).ok
    assert seen["op"] == "delete"


def test_record_ops_need_model(tmp_path, monkeypatch):
    ops, messages, _ = _connected_ops(tmp_path, monkeypatch,
                                       name="DTD")[:3]
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    inst = Instance(name="DTD2", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    ops._sessions[inst.id] = {"url": "u"}
    assert not asyncio.run(ops.rec_create(inst.id, {})).ok
    assert not asyncio.run(ops.rec_update(inst.id, 1, {})).ok
    assert not asyncio.run(ops.rec_delete(inst.id, 1, "x")).ok
    assert any("Pick a model" in m for m, _k in messages)


def test_cron_and_tools(tmp_path, monkeypatch):
    from odoo_vite.core import devtools_export, odoo_inspect

    ops, _, sinks, inst = _connected_ops(tmp_path, monkeypatch,
                                         name="DTE")
    monkeypatch.setattr(
        odoo_inspect, "list_cron_jobs",
        lambda _c: Result(ok=True, message="1",
                          data={"crons": [{"name": "C"}]}))
    assert asyncio.run(ops.cron_refresh(inst.id)).ok
    assert sinks["crons"] == [(inst.id, [{"name": "C"}])]
    monkeypatch.setattr(
        devtools_export, "generate_launch_json",
        lambda _i, **k: Result.success(message="Wrote launch.json"))
    assert asyncio.run(ops.launch_json(inst.id)).ok
    # Editors really exist on this box — stub the lookup so the test
    # never launches a window; the failure path is deterministic.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: None)
    res = asyncio.run(ops.open_editor(inst.id, "code"))
    assert not res.ok and "not found on PATH" in res.message
    bare_ops, _, _ = _ops()
    assert not asyncio.run(
        bare_ops.open_editor("no-such-id", "code")).ok
