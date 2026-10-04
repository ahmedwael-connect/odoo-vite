"""Ticket 3.2–3.5 tests: process_manager with mocked Popen/psutil.

No real Odoo processes, no network. Registry is real SQLite in tmp_path.
"""

import subprocess
import types

import psutil
import pytest

from odoo_vite.core import process_manager
from odoo_vite.core.db_state import DbState
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import get_instance, update_instance


class FakePopen:
    launched: list = []
    poll_result = None

    def __init__(self, cmd, **kwargs):
        FakePopen.launched.append((list(cmd), kwargs))
        self.cmd = cmd
        self.pid = 424242
        self.returncode = 0 if FakePopen.poll_result is None else FakePopen.poll_result

    def poll(self):
        return FakePopen.poll_result


@pytest.fixture(autouse=True)
def _clean_popen():
    FakePopen.launched = []
    FakePopen.poll_result = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


@pytest.fixture
def fake_fs(tmp_path):
    base = tmp_path / "inst"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").touch()
    return base


def _inst(base, **overrides):
    kwargs = {
        "name": "T", "version": "17.0", "path": str(base),
        "venv_path": str(base / "venv"),
        "community_path": str(base / "community"),
        "conf_path": str(base / "odoo.conf"),
        "log_path": str(base / "logs" / "odoo.log"),
        "port": 8079, "db_user": "odoo", "db_password": "odoo",
        "primary_db": "t_db", "status": "stopped",
    }
    kwargs.update(overrides)
    return Instance(**kwargs)


@pytest.fixture
def _no_collision(monkeypatch):

    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=False,
                                              initialized=False))

@pytest.fixture
def _popen(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    return FakePopen


def _register(inst, db):
    from odoo_vite.core.registry import create_instance

    assert create_instance(inst, db).ok


# ------------------------------------------------------------- start
def test_first_start_uses_i_base_and_confirms(tmp_path, db, fake_fs, _popen,
                                              _no_collision, monkeypatch):
    from odoo_vite.core import audit as audit_log

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    inst = _inst(fake_fs)
    _register(inst, db)
    previews = []
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: previews.append(pv) or True, db_path=db)
    assert res.ok, res.message
    assert len(previews) == 1
    assert previews[0]["db_name"] == "t_db"
    assert "-i" in previews[0]["command"] and "base" in previews[0]["command"]
    cmd = FakePopen.launched[0][0]
    assert cmd[cmd.index("-d") + 1] == "t_db"
    assert "-i" in cmd
    assert FakePopen.launched[0][1].get("start_new_session") is True
    row = get_instance(inst.id, db)
    assert row.status == "running" and row.pid == 424242 and row.db_created is True
    actions = [e["action"] for e in audit_log.read_events()]
    assert "start" in actions and "db_create" in actions


def test_first_start_without_confirm_cb_refuses(tmp_path, db, fake_fs, _popen,
                                               _no_collision, monkeypatch):
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(inst.id, confirm_cb=None, db_path=db)
    assert not res.ok
    assert res.data.get("needs_confirm") is True
    assert FakePopen.launched == []


def test_first_start_declined_aborts(db, fake_fs, _popen, _no_collision, monkeypatch):
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: False, db_path=db)
    assert not res.ok and "cancelled" in res.message.lower()
    assert FakePopen.launched == []
    assert get_instance(inst.id, db).status == "stopped"


def test_collision_returns_reuse_shape(db, fake_fs, _popen, monkeypatch):

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=True))
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert not res.ok
    assert res.data.get("collision") is True
    assert res.data.get("db_name") == "t_db"
    assert FakePopen.launched == []


def test_exists_but_empty_db_reinits_without_collision(
        db, fake_fs, _popen, monkeypatch):

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=False))
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert res.ok, res.message
    cmd = FakePopen.launched[0][0]
    assert "-i" in cmd  # -i base completes the interrupted init


def test_second_start_skips_i_base_and_confirm(db, fake_fs, _popen, monkeypatch):

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    # healthy second start: DB exists AND is initialized (else the recovery
    # confirm flow correctly engages — see BUG-3).
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=True))
    inst = _inst(fake_fs, db_created=True)
    _register(inst, db)
    res = process_manager.start_instance(inst.id, confirm_cb=None, db_path=db)
    assert res.ok, res.message
    cmd = FakePopen.launched[0][0]
    assert "-i" not in cmd
    assert cmd[cmd.index("-d") + 1] == "t_db"


def test_uninitialized_db_reengages_confirm(db, fake_fs, _popen, monkeypatch):
    """BUG-3: db_created=True but DB empty/gone → confirm + -i, not plain -d."""

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=False,
                                              initialized=False))
    inst = _inst(fake_fs, db_created=True)
    _register(inst, db)
    refused = process_manager.start_instance(inst.id, confirm_cb=None, db_path=db)
    assert not refused.ok and refused.data.get("needs_confirm") is True
    assert FakePopen.launched == []
    previews = []
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: previews.append(pv) or True, db_path=db)
    assert res.ok, res.message
    assert previews[0].get("reinit") is False  # missing DB: fresh create
    assert "-i" in FakePopen.launched[0][0]


def test_auto_update_modules_appended(db, fake_fs, _popen, _no_collision, monkeypatch):
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    inst = _inst(fake_fs, auto_update_modules=["sale", " stock "])
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert res.ok, res.message
    cmd = FakePopen.launched[0][0]
    assert cmd[cmd.index("-u") + 1] == "sale,stock"


def test_pending_update_merged_and_consumed(db, fake_fs, _popen, _no_collision,
                                            monkeypatch):
    """3.2.0 F1: the one-shot queue joins -u (deduped) and clears after a
    launch that stays alive; the every-start list is left alone."""
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    inst = _inst(fake_fs, auto_update_modules=["sale"],
                 pending_update_modules=["stock", " sale "])
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert res.ok, res.message
    cmd = FakePopen.launched[0][0]
    assert cmd[cmd.index("-u") + 1] == "sale,stock"
    row = get_instance(inst.id, db)
    assert row.pending_update_modules == []
    assert row.auto_update_modules == ["sale"]


def test_pending_survives_immediate_crash(db, fake_fs, _popen, _no_collision,
                                          monkeypatch):
    """Consume-on-success only: a launch that dies instantly keeps the queue."""
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    FakePopen.poll_result = 1
    inst = _inst(fake_fs, pending_update_modules=["stock"])
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert not res.ok
    row = get_instance(inst.id, db)
    assert row.pending_update_modules == ["stock"]


def test_build_command_pending_can_be_excluded(fake_fs):
    """Standalone initialize_database must not drain the start queue."""
    inst = _inst(fake_fs, auto_update_modules=["sale"],
                 pending_update_modules=["stock"])
    cmd = process_manager._build_command(inst, "d", True,
                                         include_pending=False)
    assert cmd[cmd.index("-u") + 1] == "sale"
    full = process_manager._build_command(inst, "d", True)
    assert full[full.index("-u") + 1] == "sale,stock"


def test_already_running_refuses(db, fake_fs, _popen, monkeypatch):
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: 99999)
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(inst.id, db_path=db)
    assert not res.ok and "already running" in res.message
    assert FakePopen.launched == []


def test_immediate_crash_marks_error(db, fake_fs, _popen, _no_collision, monkeypatch):
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    FakePopen.poll_result = 1
    inst = _inst(fake_fs)
    _register(inst, db)
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert not res.ok
    row = get_instance(inst.id, db)
    assert row.status == "error" and row.last_error


# -------------------------------------------------------------- stop
class FakeProc:
    def __init__(self, graceful=True, match=True):
        self.graceful = graceful
        self.match = match
        self.term_called = False
        self.kill_called = False
        self._alive = True

    def is_running(self):
        return self._alive

    def cmdline(self):
        if self.match:
            return ["/x/venv/bin/python", "/x/community/odoo-bin",
                    "-c", "/x/odoo.conf", "-d", "t_db"]
        return ["/usr/bin/something-else"]

    def terminate(self):
        self.term_called = True
        if self.graceful:
            self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            raise psutil.TimeoutExpired(timeout)
        return 0

    def kill(self):
        self.kill_called = True
        self._alive = False


def _running_row(inst, db, pid=31337):
    _register(inst, db)
    assert update_instance(inst.id, db, status="running", pid=pid).ok


def test_stop_graceful(db, fake_fs, monkeypatch):
    proc = FakeProc(graceful=True)
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(process_manager, "_cmdline_matches", lambda *a: True)
    inst = _inst(fake_fs)
    _running_row(inst, db)
    res = process_manager.stop_instance(inst.id, db_path=db)
    assert res.ok and "stopped" in res.message.lower()
    assert proc.term_called and not proc.kill_called
    row = get_instance(inst.id, db)
    assert row.status == "stopped" and row.pid is None


def test_stop_timeout_kills(db, fake_fs, monkeypatch):
    proc = FakeProc(graceful=False)
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(process_manager, "_cmdline_matches", lambda *a: True)
    inst = _inst(fake_fs)
    _running_row(inst, db)
    res = process_manager.stop_instance(inst.id, timeout=1, db_path=db)
    assert res.ok and "SIGKILL" in res.message
    assert proc.kill_called
    assert get_instance(inst.id, db).status == "stopped"


def test_stop_stale_pid_corrects_without_signalling(db, fake_fs, monkeypatch):
    proc = FakeProc(match=False)
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    inst = _inst(fake_fs)
    _running_row(inst, db)
    res = process_manager.stop_instance(inst.id, db_path=db)
    assert res.ok and "stale pid" in res.message.lower()
    assert not proc.term_called and not proc.kill_called
    assert get_instance(inst.id, db).status == "stopped"


def test_stop_already_stopped(db, fake_fs):
    inst = _inst(fake_fs)
    _register(inst, db)
    assert process_manager.stop_instance(inst.id, db_path=db).ok


# ----------------------------------------------------------- restart
def test_restart_composes_stop_and_start(db, fake_fs, _popen, monkeypatch):

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=True))
    inst = _inst(fake_fs, db_created=True)
    _register(inst, db)
    res = process_manager.restart_instance(inst.id, db_path=db)
    assert res.ok, res.message
    assert len(FakePopen.launched) == 1
    assert get_instance(inst.id, db).status == "running"


# ------------------------------------------------------------ statuses
def test_get_statuses_downgrades_dead_pid(db, fake_fs, monkeypatch):
    from odoo_vite.core import audit as audit_log

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    inst = _inst(fake_fs)
    _running_row(inst, db, pid=999998)
    out = process_manager.get_statuses(db_path=db)
    assert out[0]["status"] == "stopped" and out[0]["pid"] is None
    assert get_instance(inst.id, db).status == "stopped"
    assert any(e["action"] == "process_gone" for e in audit_log.read_events())


def test_get_statuses_reports_live_stats(db, fake_fs, monkeypatch):
    import contextlib

    inst = _inst(fake_fs)
    _running_row(inst, db, pid=424242)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: 424242)

    class P:
        def oneshot(self):
            return contextlib.nullcontext()

        def cpu_percent(self, interval=None):
            return 12.5

        def memory_info(self):
            return types.SimpleNamespace(rss=256 * 1048576)

    monkeypatch.setattr(psutil, "Process", lambda pid: P())
    out = process_manager.get_statuses(db_path=db)
    assert out[0]["status"] == "running"
    assert out[0]["cpu_percent"] == 12.5
    assert out[0]["memory_mb"] == 256.0


def test_database_exists_live_or_skip():
    from odoo_vite.core import db_manager

    if not db_manager.server_reachable():
        pytest.skip("no local postgres for live probe")
    assert db_manager.database_exists("postgres", "odoo", "odoo") is True
    assert db_manager.database_exists("no_such_db_xyz_123") is False
