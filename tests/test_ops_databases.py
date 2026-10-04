"""PSS-4: database ops failure paths + Qt grouping parity (no PG)."""

import asyncio



from odoo_vite.ops.databases import (  # noqa: E402
    DatabaseOps,
    group_discover_entries,
    pick_open_file,
    pick_save_file,
)


def _ops():
    messages = []
    ops = DatabaseOps(lambda m, k="info": messages.append((m, k)))
    return ops, messages


def _entries():
    return [
        {"name": "db_new", "initialized": True, "odoo_major": "17.0"},
        {"name": "db_old", "initialized": True, "odoo_major": "16.0"},
        {"name": "db_raw", "initialized": False, "odoo_major": ""},
        {"name": "db_new2", "initialized": True, "odoo_major": "17.0"},
    ]


def test_grouping_matches_qt():
    """Grouping rule, frozen at cutover (was byte-parity-tested against
    the Qt flow until ui_qt/ was deleted in PSS-9)."""
    assert group_discover_entries(_entries(), "17.0") == {
        "likely": ["db_new", "db_new2"],
        "other": ["db_old"],
        "plain": ["db_raw"],
    }
    assert group_discover_entries([], "17.0") == {
        "likely": [], "other": [], "plain": []}


def test_unknown_ids_fail_quietly():
    ops, messages = _ops()
    assert asyncio.run(ops.refresh_states("no-such-id")) == {}
    assert asyncio.run(ops.validate("no-such-id")) == {}
    payload = asyncio.run(ops.discover_entries("no-such-id"))
    assert payload["ok"] is False
    res = asyncio.run(ops.track("no-such-id", "db"))
    assert not res.ok
    res = asyncio.run(ops.drop_db("no-such-id", "db"))
    assert not res.ok
    res = asyncio.run(ops.set_primary("no-such-id", "db"))
    assert not res.ok
    assert any("not found" in m for m, _k in messages)


def test_pickers_empty_without_zenity(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: None)
    assert pick_save_file("T", "x.dump", "*.dump") == ""
    assert pick_open_file("T", "*.dump") == ""


def test_track_many_reports_batch_failure(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "dbops.db"))
    inst = Instance(name="Batch", version="17.0", path=str(tmp_path),
                    primary_db="main", tracked_dbs=["main"])
    assert create_instance(inst).ok
    ops, messages = _ops()
    # "ghost" matches no live role without PG — exercises the batch path.
    res = asyncio.run(ops.track_many(inst.id, ["ghost"]))
    assert isinstance(res.message, str) and messages


def test_reconcile_timer_tracks_enabled_schedules(monkeypatch):
    """3.1.0 B8: timer installs while a schedule is enabled, is removed
    when the last one goes away, and stays untouched otherwise."""
    from odoo_vite.core.result import Result
    from odoo_vite.ops import databases as ops_mod

    calls: list[str] = []
    state = {"enabled": True, "installed": False, "timer_enabled": False}

    class _S:
        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled

    monkeypatch.setattr(
        ops_mod.backup_scheduler, "list_schedules",
        lambda *a, **k: [_S(state["enabled"])])
    monkeypatch.setattr(
        ops_mod.backup_scheduler, "timer_status",
        lambda: {"installed": state["installed"],
                 "enabled": state["timer_enabled"]})
    monkeypatch.setattr(
        ops_mod.backup_scheduler, "install_timer",
        lambda: calls.append("install") or Result.success(message="ok"))
    monkeypatch.setattr(
        ops_mod.backup_scheduler, "remove_timer",
        lambda: calls.append("remove") or Result.success(message="ok"))

    ops, _messages = _ops()

    asyncio.run(ops._reconcile_timer())          # enabled, no timer -> install
    assert calls == ["install"]

    calls.clear()
    state.update(installed=True, timer_enabled=True)
    asyncio.run(ops._reconcile_timer())          # healthy -> untouched
    assert calls == []

    calls.clear()
    state.update(enabled=False)
    asyncio.run(ops._reconcile_timer())          # none enabled -> remove
    assert calls == ["remove"]

    calls.clear()
    state.update(installed=False)
    asyncio.run(ops._reconcile_timer())          # nothing to do
    assert calls == []


def test_streamed_db_ops_guards_and_kwarg_passthrough(tmp_path, monkeypatch):
    """3.2.0: init/backup/restore forward progress_cb/cancel — the
    _progress_call streaming contract — and fail cleanly for unknown ids."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance
    from odoo_vite.core.result import Result
    from odoo_vite.ops import databases as db_mod

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "stream.db"))
    ops, _messages = _ops()

    # unknown instance — the pw/instance guards fire before any tool runs
    res = asyncio.run(ops.backup_db("no-such", "db", "/tmp/x.dump"))
    assert not res.ok and "not found" in res.message
    res = asyncio.run(ops.restore_db("no-such", "/tmp/x.dump", "db"))
    assert not res.ok and "not found" in res.message
    res = asyncio.run(ops.init_db("no-such", "db"))
    assert not res.ok  # initialize_database's own id guard

    seen: dict = {}

    def fake_init(iid, db_name, progress_cb=None, cancel=None, db_path=None):
        seen["init"] = (progress_cb, cancel)
        return Result.success(message="init ok")

    def fake_backup(db_name, dest, progress_cb=None, cancel=None, **kw):
        seen["backup"] = (progress_cb, cancel)
        return Result.success(message="backup ok")

    def fake_restore(dump, target, progress_cb=None, cancel=None, **kw):
        seen["restore"] = (progress_cb, cancel)
        return Result.success(message="restore ok")

    monkeypatch.setattr(db_mod.process_manager, "initialize_database",
                        fake_init)
    monkeypatch.setattr(db_mod.db_backup, "backup_database", fake_backup)
    monkeypatch.setattr(db_mod.db_backup, "restore_database", fake_restore)

    def _cb(_line):
        return None

    def _cancel():
        return False

    inst = Instance(name="Stream", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok

    assert asyncio.run(ops.init_db(inst.id, "db",
                                   progress_cb=_cb, cancel=_cancel)).ok
    assert asyncio.run(ops.backup_db(inst.id, "db", "/tmp/x.dump",
                                     progress_cb=_cb, cancel=_cancel)).ok
    assert asyncio.run(ops.restore_db(inst.id, "/tmp/x.dump", "db",
                                      progress_cb=_cb, cancel=_cancel)).ok
    assert seen["init"] == (_cb, _cancel)
    assert seen["backup"] == (_cb, _cancel)
    assert seen["restore"] == (_cb, _cancel)
