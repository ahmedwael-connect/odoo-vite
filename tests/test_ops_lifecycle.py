"""Slint ops tests (PSS-3/4): async core drivers with REAL backends.

Framework-free by construction (no bridge/component/dialog imports): per
docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. asyncio.run() drives the coroutines on the main
thread; to_thread workers touch only plain core data — the same shape
the Qt suite has run green for years.
"""

import asyncio
import threading



from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ops.lifecycle import LifecycleOps  # noqa: E402


def _ops():
    messages, refreshes = [], []
    ops = LifecycleOps(lambda m, k="info": messages.append((m, k)),
                       lambda: refreshes.append(1))
    return ops, messages, refreshes


def test_unknown_ids_fail_without_side_effects():
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.stop("no-such-id"))
    assert not res.ok and "no-such-id" in res.message
    res = asyncio.run(ops.remove("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.clone("no-such-id", "X"))
    assert not res.ok and "no-such-id" in res.message
    assert len(messages) == 3
    # clone bails before touching anything: no refresh for a no-op.
    assert refreshes == [1, 1]


def test_start_runs_off_ui_thread(monkeypatch):
    """asyncio.to_thread replaces QThread: core runs off-main, sinks fire."""
    from odoo_vite.core import process_manager

    seen = {}

    def _fake_start(instance_id, database=None, confirm_cb=None):
        seen["off_main"] = (
            threading.current_thread() is not threading.main_thread())
        seen["args"] = (instance_id, database)
        return Result(ok=True, message="Started", data={})

    monkeypatch.setattr(process_manager, "start_instance", _fake_start)
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.start("iid-1", "mydb"))
    assert res.ok
    assert seen == {"off_main": True, "args": ("iid-1", "mydb")}
    assert messages == [("Started", "info")] and refreshes == [1]


def test_needs_confirm_passes_through(monkeypatch):
    from odoo_vite.core import process_manager

    preview = {"detail": "create db foo?"}
    monkeypatch.setattr(
        process_manager, "start_instance",
        lambda *a, **k: Result(ok=False, message="Need confirm",
                               data={"needs_confirm": True,
                                     "preview": preview}))
    ops, messages, _ = _ops()
    res = asyncio.run(ops.start("iid-1"))
    assert res.data["needs_confirm"] is True
    assert res.data["preview"] == preview


def test_clone_resolves_password_on_caller_thread(monkeypatch, tmp_path):
    """Qt dbus rule kept: keyring read happens before to_thread."""
    import odoo_vite.ops.lifecycle as lifecycle_mod
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "lc.db"))
    inst = Instance(name="LC", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    seen = {}
    real_get = lifecycle_mod.get_db_password

    def _tracking_get(inst_arg):
        seen["main"] = (threading.current_thread()
                        is threading.main_thread())
        return real_get(inst_arg)

    monkeypatch.setattr(lifecycle_mod, "get_db_password", _tracking_get)
    monkeypatch.setattr(
        lifecycle_mod.clone_core, "clone_instance",
        lambda *a, **k: Result(ok=True, message="Cloned", data={}))
    ops, _, _ = _ops()
    res = asyncio.run(ops.clone(inst.id, "Copy", 8071))
    assert res.ok
    assert seen.get("main") is True


def test_set_primary_round_trip_tmp_registry(monkeypatch, tmp_path):
    """Real sqlite write on a worker, Slint-free (control-proven clean)."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance, get_instance
    from odoo_vite.ops.databases import DatabaseOps

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "ops.db"))
    inst = Instance(name="OPS", version="17.0", path=str(tmp_path),
                    primary_db="main", tracked_dbs=["main", "extra"])
    assert create_instance(inst).ok
    ops = DatabaseOps(lambda m, k="info": None)
    res = asyncio.run(ops.set_primary(inst.id, "extra"))
    assert res.ok, res.message
    assert get_instance(inst.id).primary_db == "extra"


# ------------------------------------------------------------------- bulk


def _statuses(*rows):
    return [{"id": i, "name": n, "status": s} for i, n, s in rows]


def test_start_many_sequential_batch(monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses",
        lambda *a, **k: _statuses(
            ("a", "Alpha", "running"), ("b", "Beta", "stopped"),
            ("c", "Gamma", "stopped")))
    calls, off_main = [], []

    def _start(iid, database=None, confirm_cb=None):
        calls.append(iid)
        off_main.append(threading.current_thread() is not threading.main_thread())
        if iid == "c":
            return Result.failure(
                "confirm needed", data={"needs_confirm": True})
        return Result(ok=True, message=f"started {iid}")

    monkeypatch.setattr(process_manager, "start_instance", _start)
    ops, messages, refreshes = _ops()
    lines = []
    res = asyncio.run(
        ops.start_many(["a", "b", "c"], progress_cb=lines.append))
    # a skipped (running), b then c attempted in order, off the main thread
    assert calls == ["b", "c"] and all(off_main)
    # needs_confirm is a skip, not a failure
    assert res.ok is True
    assert res.data == {
        "done": ["Beta"], "skipped": ["Alpha"],
        "needs_confirm": ["Gamma"], "failed": []}
    assert "started 1" in res.message
    assert "skipped 1 already running" in res.message
    assert "database confirmation" in res.message
    assert lines == [
        "[1/3] Alpha: skipped (already running)",
        "[2/3] Starting Beta…", "[2/3] Beta: started b",
        "[3/3] Starting Gamma…", "[3/3] Gamma: confirm needed",
    ]
    assert messages == [(res.message, "info")]
    assert refreshes == [1]


def test_start_many_failure_reports_and_refreshes(monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses",
        lambda *a, **k: _statuses(("x", "Xray", "stopped")))
    monkeypatch.setattr(
        process_manager, "start_instance",
        lambda *a, **k: Result.failure("port 8069 busy"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.start_many(["x", "ghost"]))
    assert res.ok is False
    assert "2 failed" in res.message and "port 8069 busy" in res.message
    assert "ghost" in res.message and "not found" in res.message
    assert res.data["failed"] == [
        {"name": "Xray", "reason": "port 8069 busy"},
        {"name": "ghost", "reason": "not found"},
    ]
    assert messages == [(res.message, "error")] and refreshes == [1]


def test_start_many_cancel_stops_early(monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses",
        lambda *a, **k: _statuses(
            ("0", "N0", "stopped"), ("1", "N1", "stopped"),
            ("2", "N2", "stopped")))
    calls = []
    monkeypatch.setattr(
        process_manager, "start_instance",
        lambda iid, *a, **k: calls.append(iid) or Result(ok=True, message="ok"))
    state = {"n": 0}

    def cancel() -> bool:
        state["n"] += 1
        return state["n"] > 1  # allow the first, refuse the rest

    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.start_many(["0", "1", "2"], cancel=cancel))
    assert calls == ["0"]
    assert "cancelled (2 remaining)" in res.message
    assert res.ok is True  # cancel is not a failure
    assert messages == [(res.message, "info")] and refreshes == [1]


def test_stop_many_skips_not_running(monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses",
        lambda *a, **k: _statuses(
            ("a", "Alpha", "running"), ("b", "Beta", "stopped")))
    calls = []
    monkeypatch.setattr(
        process_manager, "stop_instance",
        lambda iid, *a, **k: calls.append(iid) or Result(ok=True, message="stopped"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.stop_many(["a", "b"]))
    assert calls == ["a"]
    assert res.ok is True
    assert "stopped 1" in res.message
    assert "skipped 1 not running" in res.message
    assert res.data == {
        "done": ["Alpha"], "skipped": ["Beta"],
        "needs_confirm": [], "failed": []}


def test_bulk_empty_ids_is_noop(monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(process_manager, "get_statuses", lambda *a, **k: [])
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.start_many([]))
    assert res.ok and res.message == "nothing to do"
    assert messages == [("nothing to do", "info")] and refreshes == [1]
