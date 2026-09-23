"""Sprint 10 Part 1 tests: RPC client (mocked transport), inspector shaping,
export content, editor detection."""

import urllib.error
import xmlrpc.client

import pytest

from odoo_vite.core import odoo_rpc
from odoo_vite.core.result import Result


class FakeCommon:
    def __init__(self, script):
        self.script = script

    def version(self):
        return self.script["version"]()

    def authenticate(self, *args):
        return self.script["authenticate"]()


class FakeObject:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def execute_kw(self, *args):
        self.calls.append(args)
        return self.script["execute_kw"]()


@pytest.fixture
def rpc_ok(monkeypatch):
    script = {
        "version": lambda: {"server_version": "17.0"},
        "authenticate": lambda: 2,
        "execute_kw": lambda: [{"id": 1, "name": "res.partner"}],
    }
    fakes = {}

    def _proxies(url, timeout=15):
        common = FakeCommon(script)
        obj = FakeObject(script)
        fakes["obj"] = obj
        return common, obj

    monkeypatch.setattr(odoo_rpc, "_proxies", _proxies)
    return fakes, script


def test_authenticate_ok_and_empty_uid(rpc_ok, monkeypatch):
    res = odoo_rpc.authenticate("http://x:8069", "db", "admin", "admin")
    assert res.ok and res.data["uid"] == 2

    fakes, script = rpc_ok
    script["authenticate"] = lambda: False
    res = odoo_rpc.authenticate("http://x:8069", "db", "admin", "wrong")
    assert not res.ok and "uid came back empty" in res.message


def test_fault_mapping(monkeypatch):
    def _boom(*a, **k):
        raise xmlrpc.client.Fault(1, "Access Denied")

    monkeypatch.setattr(odoo_rpc, "_proxies",
                        lambda *a, **k: (_boom_proxy(_boom), _boom_proxy(_boom)))
    res = odoo_rpc.authenticate("http://x:8069", "db", "admin", "bad")
    assert not res.ok and "Postgres role" in res.message  # points at Odoo user


def _boom_proxy(fn):
    class _P:
        def __getattr__(self, name):
            return fn

    return _P()


def test_connection_refused_mapping(monkeypatch):
    def _down(*a, **k):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

    monkeypatch.setattr(odoo_rpc, "_proxies",
                        lambda *a, **k: (_boom_proxy(_down), _boom_proxy(_down)))
    res = odoo_rpc.server_version("http://x:8069")
    assert not res.ok and "start it first" in res.message


def test_search_read_pagination(rpc_ok):
    fakes, script = rpc_ok
    res = odoo_rpc.search_read("http://x", "db", 2, "pw", "res.partner",
                               domain=[[["name", "ilike", "a"]]],
                               fields=["name"], offset=10, limit=500)
    assert res.ok
    args = fakes["obj"].calls[0]
    assert args[4] == "search_read"
    assert args[6]["offset"] == 10 and args[6]["limit"] == 200  # clamped


def test_search_read_passes_domain_through(rpc_ok):
    """Domains pass through untouched (a prior normalization layer corrupted
    valid tuple domains by adding a nesting level — pinned against regress)."""
    fakes, script = rpc_ok
    domain = [("name", "ilike", "a")]
    res = odoo_rpc.search_read("http://x", "db", 2, "pw", "res.partner",
                               domain=domain)
    assert res.ok
    sent_args = fakes["obj"].calls[0][5]
    assert sent_args == [domain]
    assert sent_args[0][0] == ("name", "ilike", "a")


def test_connect_requires_running_and_credss(tmp_path, monkeypatch):
    from odoo_vite.core import process_manager
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    db = str(tmp_path / "r.db")
    inst = Instance(name="D", path="/tmp/d", port=8069, primary_db="d_db",
                    status="stopped")
    assert create_instance(inst, db).ok
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    res = odoo_rpc.connect_instance(inst, db_path=db)
    assert not res.ok and "not running" in res.message

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: 999)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="d_db", exists=True, initialized=True))
    res = odoo_rpc.connect_instance(inst, db_path=db)
    assert not res.ok and res.data.get("needs_credentials") is True


# ------------------------------------------------------- inspector shaping
def _client():
    return {"url": "http://x:8069", "db": "d", "uid": 2, "password": "pw"}


def test_list_models_and_metadata(monkeypatch):
    from odoo_vite.core import odoo_inspect

    calls = []

    def _fake(url, db, uid, pw, model, method, args=None, kwargs=None,
              timeout=15):
        calls.append((model, method, args))
        if model == "ir.model":
            return Result.success(data=[{"model": "res.partner",
                                         "name": "Contact",
                                         "info": "i"}])
        if model == "ir.model.fields":
            return Result.success(data=[{"name": "name", "ttype": "char",
                                         "required": True}])
        if model == "ir.model.constraint":
            return Result.success(data=[])
        if model == "ir.model.access":
            return Result.success(data=[{"name": "r", "group_id": False,
                                         "perm_read": True}])
        raise AssertionError(model)

    monkeypatch.setattr(odoo_rpc, "execute_kw", _fake)
    res = odoo_inspect.list_models(_client())
    assert res.ok and res.data["models"][0]["technical"] == "res.partner"
    res = odoo_inspect.get_model_metadata(_client(), "res.partner")
    assert res.ok
    assert res.data["fields"][0]["name"] == "name"
    assert res.data["access"][0]["perm_read"] is True
    assert not odoo_inspect.get_model_metadata(_client(), "").ok


def test_cron_shaping(monkeypatch):
    from odoo_vite.core import odoo_inspect

    def _fake(url, db, uid, pw, model, method, args=None, kwargs=None,
              timeout=15):
        if model == "ir.cron" and method == "fields_get":
            return Result.success(data={
                "cron_name": {}, "active": {}, "nextcall": {},
                "interval_number": {}, "interval_type": {},
                "ir_actions_server_id": {}})
        if model == "ir.cron":
            return Result.success(data=[{
                "id": 5, "cron_name": "GC", "active": True,
                "nextcall": "2026-01-01 00:00:00", "interval_number": 1,
                "interval_type": "days", "ir_actions_server_id": [9, "GC action"]}])
        if model == "ir.actions.server":
            return Result.success(data=[{
                "id": 9, "model_id": [1, "ir.cron"], "state": "code",
                "code": "model.run()"}])
        raise AssertionError(model)

    monkeypatch.setattr(odoo_rpc, "execute_kw", _fake)
    res = odoo_inspect.list_cron_jobs(_client())
    assert res.ok
    job = res.data["crons"][0]
    assert job["name"] == "GC" and job["model"] == "ir.cron"
    assert job["interval"] == "1 days" and "run()" in job["function"]


def test_cron_legacy_shape(monkeypatch):
    """15.0/16.0-style rows (name/model_id/function inline) still map."""
    from odoo_vite.core import odoo_inspect

    def _fake(url, db, uid, pw, model, method, args=None, kwargs=None,
              timeout=15):
        if model == "ir.cron" and method == "fields_get":
            return Result.success(data={
                "name": {}, "model_id": {}, "function": {}, "active": {},
                "nextcall": {}, "interval_number": {}, "interval_type": {}})
        return Result.success(data=[{
            "id": 5, "name": "Old", "active": True,
            "nextcall": "2026-01-01 00:00:00", "interval_number": 2,
            "interval_type": "weeks", "model_id": [3, "res.partner"],
            "function": "do_thing"}])

    monkeypatch.setattr(odoo_rpc, "execute_kw", _fake)
    res = odoo_inspect.list_cron_jobs(_client())
    assert res.ok
    job = res.data["crons"][0]
    assert (job["name"], job["model"], job["function"]) == (
        "Old", "res.partner", "do_thing")


def test_record_crud_guards(monkeypatch):
    from odoo_vite.core import odoo_inspect

    assert not odoo_inspect.create_record(_client(), "m", {}).ok
    assert not odoo_inspect.update_record(_client(), "m", 1, {}).ok
    assert not odoo_inspect.delete_record(_client(), "m", "xx").ok

    def _fake(url, db, uid, pw, model, method, args=None, kwargs=None,
              timeout=15):
        if method == "exists":
            return Result.success(data=[])
        return Result.success(data=True)

    monkeypatch.setattr(odoo_rpc, "execute_kw", _fake)
    res = odoo_inspect.delete_record(_client(), "m", 42)
    assert not res.ok and "already deleted" in res.message


# ------------------------------------------------------- export
def test_launch_json_content(tmp_path):
    from odoo_vite.core import devtools_export as ex
    from odoo_vite.core.instance import Instance

    inst = Instance(name="Dbg", path=str(tmp_path), venv_path=str(tmp_path / "v"),
                    community_path=str(tmp_path / "odoo"),
                    conf_path=str(tmp_path / "odoo.conf"), primary_db="d",
                    port=8069)
    res = ex.generate_launch_json(inst)
    assert res.ok, res.message
    import json as _json

    data = _json.loads(open(res.data["path"]).read())
    cfg = data["configurations"][0]
    assert cfg["connect"]["port"] == 5678
    assert cfg["pathMappings"][0]["localRoot"] == str(tmp_path / "odoo")
    assert "debugpy" in cfg["comment"] and "attach" in cfg["comment"].lower()
    assert not ex.generate_launch_json(inst, debug_port=99999).ok


def test_editor_detection_and_open(monkeypatch, tmp_path):
    from odoo_vite.core import devtools_export as ex

    monkeypatch.setattr("odoo_vite.core.devtools_export.shutil.which",
                        lambda name: "/usr/bin/code" if name == "code" else None)
    found = ex.detect_editors()
    assert found == {"code": "/usr/bin/code"}

    launched = []
    monkeypatch.setattr("odoo_vite.core.devtools_export.subprocess.Popen",
                        lambda *a, **k: launched.append(a) or DummyProc())
    res = ex.open_in_editor("code", str(tmp_path))
    assert res.ok and launched[0][0] == ["/usr/bin/code", str(tmp_path)]
    assert not ex.open_in_editor("cursor", str(tmp_path)).ok
    assert not ex.open_in_editor("vim", str(tmp_path)).ok
    assert not ex.open_in_editor("code", "/nonexistent-xyz").ok


class DummyProc:
    pass
