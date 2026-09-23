"""Tickets 4.5/4.6 tests: switch_database + track/untrack idempotency."""

import subprocess

import pytest

from odoo_vite.core import process_manager
from odoo_vite.core.db_state import DbState
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance, update_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


def _inst(base, **overrides):
    kwargs = {"name": "S", "version": "17.0", "path": str(base),
              "venv_path": str(base / "venv"),
              "community_path": str(base / "community"),
              "conf_path": str(base / "odoo.conf"),
              "log_path": str(base / "logs" / "odoo.log"),
              "port": 8081, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "s_one", "tracked_dbs": ["s_one"],
              "status": "stopped", "db_created": True}
    kwargs.update(overrides)
    return Instance(**kwargs)


class FakePopen:
    launched: list = []
    poll_result = None

    def __init__(self, cmd, **kwargs):
        FakePopen.launched.append(list(cmd))
        self.cmd = cmd
        self.pid = 777001
        self.returncode = 0

    def poll(self):
        return FakePopen.poll_result


@pytest.fixture(autouse=True)
def _clean():
    FakePopen.launched = []
    FakePopen.poll_result = None


def _fs(base):
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").touch()


def test_switch_stopped_only_sets_primary(tmp_path, db, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = process_manager.switch_database(inst.id, "s_two", db_path=db)
    assert res.ok, res.message
    assert res.data.get("started") is False
    assert FakePopen.launched == []  # never started — no surprise starts
    assert get_instance(inst.id, db).primary_db == "s_two"


def _fake_stop_recording(calls, inst_id, db, alive=None):
    def _stop(*a, **k):
        calls.append("stop")
        if alive is not None:
            alive["on"] = False
        update_instance(inst_id, db, status="stopped", pid=None)
        return Result.success(message="stopped")
    return _stop


def test_switch_running_to_existing_db(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager

    alive = {"on": True}
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(process_manager, "_alive_pid",
                        lambda inst: 555 if alive["on"] else None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=True))
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base, status="running", pid=555)
    assert create_instance(inst, db).ok
    calls: list = []
    monkeypatch.setattr(process_manager, "stop_instance",
                        _fake_stop_recording(calls, inst.id, db, alive))
    res = process_manager.switch_database(inst.id, "s_two", db_path=db)
    assert res.ok, res.message
    assert calls == ["stop"]
    cmd = FakePopen.launched[0]
    assert cmd[cmd.index("-d") + 1] == "s_two"
    assert "-i" not in cmd  # existing DB: no creation flow
    assert get_instance(inst.id, db).primary_db == "s_two"


def test_switch_running_to_new_db_uses_confirm_flow(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager

    alive = {"on": True}
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(process_manager, "_alive_pid",
                        lambda inst: 666 if alive["on"] else None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=False,
                                              initialized=False))
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base, status="running", pid=666)
    assert create_instance(inst, db).ok
    calls: list = []
    monkeypatch.setattr(process_manager, "stop_instance",
                        _fake_stop_recording(calls, inst.id, db, alive))
    seen = {}
    res = process_manager.switch_database(
        inst.id, "brand_new",
        confirm_cb=lambda pv: seen.setdefault("preview", pv) or True,
        db_path=db)
    assert res.ok, res.message
    assert calls == ["stop"]
    assert seen["preview"]["db_name"] == "brand_new"  # confirm flow ran
    cmd = FakePopen.launched[0]
    assert "-i" in cmd and "base" in cmd  # creation flow reused
    assert get_instance(inst.id, db).db_created is True


def test_switch_invalid_name_rejected(tmp_path, db):
    inst = _inst(tmp_path / "i")
    assert create_instance(inst, db).ok
    res = process_manager.switch_database(inst.id, "Bad-Name!", db_path=db)
    assert not res.ok


# ------------------------------------------------------- track / untrack
def test_track_idempotent_and_untrack_noop(tmp_path, db):
    from odoo_vite.core.db_manager import track_database, untrack_database

    inst = _inst(tmp_path / "i")
    assert create_instance(inst, db).ok
    assert track_database(inst.id, "s_two", db).ok
    again = track_database(inst.id, "s_two", db)
    assert again.ok and again.data["tracked_dbs"].count("s_two") == 1
    assert get_instance(inst.id, db).tracked_dbs == ["s_one", "s_two"]

    noop = untrack_database(inst.id, "never_tracked", db)
    assert noop.ok
    flagged = untrack_database(inst.id, "s_one", db)
    assert flagged.ok and flagged.data["was_primary"] is True
    assert get_instance(inst.id, db).tracked_dbs == ["s_two"]
    assert track_database(inst.id, "Bad Name!", db).ok is False


def test_list_databases_live_or_skip():
    from odoo_vite.core import db_manager

    if not db_manager.server_reachable():
        pytest.skip("no local postgres for live probe")
    res = db_manager.list_databases_for_user("odoo", "odoo")
    assert res.ok, res.message
    assert "postgres" in res.data["databases"]
