"""Slint ops tests (PSS-3/4): async core drivers with REAL backends.

Slint-free by construction (no bridge/component/dialog imports): per
docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. asyncio.run() drives the coroutines on the main
thread; to_thread workers touch only plain core data — the same shape
the Qt suite has run green for years.
"""

import asyncio
import threading

import pytest

pytest.importorskip("slint", reason="slint package required")

from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ui_slint.lifecycle import LifecycleOps  # noqa: E402


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
    import odoo_vite.ui_slint.lifecycle as lifecycle_mod
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
    from odoo_vite.ui_slint.databases import DatabaseOps

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "ops.db"))
    inst = Instance(name="OPS", version="17.0", path=str(tmp_path),
                    primary_db="main", tracked_dbs=["main", "extra"])
    assert create_instance(inst).ok
    ops = DatabaseOps(lambda m, k="info": None)
    res = asyncio.run(ops.set_primary(inst.id, "extra"))
    assert res.ok, res.message
    assert get_instance(inst.id).primary_db == "extra"


def test_run_start_queues_confirm():
    """Module coroutine tested directly: needs_confirm becomes queue data."""
    import queue as queue_mod

    from odoo_vite.ui_slint.bridge import _run_start

    async def _needs_confirm(*a, **k):
        return Result(ok=False, message="Need confirm",
                      data={"needs_confirm": True,
                            "preview": {"detail": "create db?"}})

    class _StubOps:
        async def start(self, iid, database=None, confirm_cb=None):
            return await _needs_confirm()

    q: queue_mod.Queue = queue_mod.Queue()
    asyncio.run(_run_start(_StubOps(), q, "iid-9"))
    kind, payload = q.get_nowait()
    assert kind == "confirm-start"
    assert payload == ("iid-9", {"detail": "create db?"})


def test_run_switch_queues_confirm():
    import queue as queue_mod

    from odoo_vite.ui_slint.bridge import _run_switch

    async def _needs_confirm(*a, **k):
        return Result(ok=False, message="Need confirm",
                      data={"needs_confirm": True,
                            "preview": {"detail": "switch?"}})

    class _StubOps:
        async def switch_db(self, iid, db, confirm_cb=None):
            return await _needs_confirm()

    q: queue_mod.Queue = queue_mod.Queue()
    asyncio.run(_run_switch(_StubOps(), q, "iid-9", "newdb"))
    kind, payload = q.get_nowait()
    assert kind == "confirm-switch"
    assert payload[0] == "iid-9" and payload[1] == "newdb"


def test_run_picker_posts_paths(monkeypatch):
    import queue as queue_mod

    import odoo_vite.ui_slint.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "_pick_save",
                        lambda *a: "/tmp/x.dump")
    monkeypatch.setattr(bridge_mod, "pick_open_file",
                        lambda *a: "/tmp/y.dump")
    q: queue_mod.Queue = queue_mod.Queue()
    asyncio.run(bridge_mod._run_picker(q, "backup-pick", "i1", "db"))
    assert q.get_nowait() == ("backup-pick", ("i1", "db", "/tmp/x.dump"))
    asyncio.run(bridge_mod._run_picker(q, "restore-pick", "i1", "db"))
    assert q.get_nowait() == ("restore-pick", ("i1", "db", "/tmp/y.dump"))


def test_run_probe_posts_result(monkeypatch):
    import queue as queue_mod

    import odoo_vite.ui_slint.bridge as bridge_mod

    seen = []
    monkeypatch.setattr(bridge_mod, "_probe_server",
                        lambda: seen.append(1) or False)
    q: queue_mod.Queue = queue_mod.Queue()
    asyncio.run(bridge_mod._run_probe(q))
    assert q.get_nowait() == ("server", False)
    assert seen == [1]
