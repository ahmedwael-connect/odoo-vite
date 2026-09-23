"""Sprint 11 tests: shell session (fake process), debounce burst (fake
clock), run-tests flags/refusals/summary parsing."""

import io
import os
import threading
import time

import pytest

from odoo_vite.core import devwatch
from odoo_vite.core.db_state import DbState
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


def _inst(**overrides):
    kwargs = {"name": "S", "version": "17.0", "path": "/tmp/s",
              "venv_path": "/tmp/s/venv", "community_path": "/tmp/s/community",
              "conf_path": "/tmp/s/odoo.conf", "log_path": "/tmp/s/odoo.log",
              "port": 8097, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "s_db", "status": "stopped", "db_created": True}
    kwargs.update(overrides)
    return Instance(**kwargs)


# ------------------------------------------------------------------ shell
# Live machinery (PTY reader/send/stop) is exercised against a real `cat`
# process (echoes stdin like a REPL would); odoo-specific validation uses
# mocked Popen (never reached on failure paths).
def _cat_session():
    import pty as _pty
    import subprocess as _sp

    from odoo_vite.core import odoo_shell

    shell = odoo_shell.OdooShell()
    master, slave = _pty.openpty()
    proc = _sp.Popen(["cat"], stdin=slave, stdout=slave, stderr=slave,
                     start_new_session=True)
    os.close(slave)
    shell._proc = proc
    shell._master = master
    shell._exited = None
    shell._reader = threading.Thread(target=shell._drain, daemon=True)
    shell._reader.start()
    return shell, proc


def test_shell_cat_roundtrip_and_stop():
    shell, proc = _cat_session()
    assert shell.running
    assert shell.send_line("hello-pty").ok
    deadline = time.monotonic() + 10
    got: list = []
    while time.monotonic() < deadline and not any(
            "hello-pty" in ln for ln in got):
        time.sleep(0.2)
        got.extend(shell.drain_output())
    assert any("hello-pty" in ln for ln in got), got
    assert shell.stop().ok
    assert not shell.running
    assert shell.send_line("late").ok is False


def test_shell_start_uses_pty_session(tmp_path, db, monkeypatch):
    """start() allocates a real PTY and spawns (mocked) odoo-bin on it."""
    import pty as _pty

    import odoo_vite.core.odoo_shell as sh
    from odoo_vite.core.db_state import DbState

    real_openpty = _pty.openpty
    used_pty = {}

    def _spy_openpty():
        import os as _os

        master, slave = real_openpty()
        used_pty["master"] = master
        used_pty["slave"] = slave
        # Keep an extra slave dup open: with the real slave closed by
        # start(), master reads would EOF instantly and the reader would
        # (correctly per its contract) mark the session exited — racing
        # the assertions below. A live child holds its own slave dup open,
        # so pinning one here models production faithfully.
        used_pty["keepalive"] = _os.dup(slave)
        return master, slave

    monkeypatch.setattr(sh.pty, "openpty", _spy_openpty)

    launched = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            launched.append((cmd, kwargs))
            self.pid = 777001
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(sh.subprocess, "Popen", FakePopen)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: DbState(
                            db_name="s_db", exists=True, initialized=True))
    base = tmp_path / "s"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "odoo.conf").write_text("[options]\n")
    inst = _inst(path=str(base), venv_path=str(base / "venv"),
                 community_path=str(base / "community"),
                 conf_path=str(base / "odoo.conf"))
    assert create_instance(inst, db).ok
    from odoo_vite.core.registry import get_instance

    from odoo_vite.core import odoo_shell

    shell = odoo_shell.OdooShell()
    res = shell.start(get_instance(inst.id, db), db_path=db)
    assert res.ok, res.message
    assert "master" in used_pty, "must allocate a PTY (pipes cannot host odoo shell)"
    cmd, kwargs = launched[0]
    assert "shell" in cmd  # odoo-bin shell args
    assert shell.running
    assert shell.send_line("1+1").ok
    assert shell.stop().ok
    assert not shell.running
    import os as _os

    for fd in (used_pty["master"], used_pty["slave"], used_pty["keepalive"]):
        try:
            _os.close(fd)
        except OSError:
            pass


def test_shell_validates_before_spawning(tmp_path, db, monkeypatch):
    from odoo_vite.core import odoo_shell

    base = tmp_path / "s"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "odoo.conf").write_text("[options]\n")
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: DbState(db_name="s_db"))
    base = tmp_path / "s"
    inst = _inst(path=str(base), venv_path=str(base / "venv"),
                 community_path=str(base / "community"),
                 conf_path=str(base / "odoo.conf"), primary_db="missing_db")
    assert create_instance(inst, db).ok
    from odoo_vite.core.registry import get_instance

    shell = odoo_shell.OdooShell()
    res = shell.start(get_instance(inst.id, db), db_path=db)
    assert not res.ok and "does not exist" in res.message


def test_shell_double_start_refused(tmp_path, db, monkeypatch):
    from odoo_vite.core import odoo_shell

    base = tmp_path / "s"
    inst = _inst(path=str(base))
    assert create_instance(inst, db).ok
    from odoo_vite.core.registry import get_instance

    shell, proc = _cat_session()
    try:
        assert shell.running
        res = shell.start(get_instance(inst.id, db), db_path=db)
        assert not res.ok and "already running" in res.message
    finally:
        shell.stop()
        proc.terminate()


# ------------------------------------------------------------------ debounce
class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_burst_feeds_single_restart():
    clock = FakeClock()
    ctrl = devwatch.DebounceController(quiet_seconds=0.8, clock=clock)
    fires = []
    for _ in range(25):  # save-storm / git checkout burst
        ctrl.feed()
        clock.advance(0.05)
    assert not ctrl.check(lambda: fires.append(1))
    clock.advance(0.8)
    assert ctrl.check(lambda: fires.append(1)) is True
    assert fires == [1] and ctrl.fires == 1
    assert ctrl.check(lambda: fires.append(1)) is False  # disarmed: no double fire


def test_separate_bursts_fire_twice():
    clock = FakeClock()
    ctrl = devwatch.DebounceController(quiet_seconds=0.8, clock=clock)
    fires = []
    ctrl.feed()
    clock.advance(1.0)
    assert ctrl.check(lambda: fires.append(1)) is True
    ctrl.feed()
    clock.advance(1.0)
    assert ctrl.check(lambda: fires.append(1)) is True
    assert ctrl.fires == 2


def test_should_watch_filters():
    assert devwatch.should_watch("/a/mod/models/x.py")
    assert devwatch.should_watch("/a/views/v.xml")
    assert devwatch.should_watch("/a/static/app.js")
    assert devwatch.should_watch("/a/data/d.csv")
    assert not devwatch.should_watch("/a/__pycache__/x.pyc")
    assert not devwatch.should_watch("/a/.git/x.py")
    assert not devwatch.should_watch("/a/image.png")
    # dot-LEAF names are skipped …
    assert not devwatch.should_watch("/a/y/.hidden.py")
    # … while dot-PARENTS (other than known junk dirs) no longer
    # disqualify the path (see ~/.local regression pins below).
    assert devwatch.should_watch("/home/ahmed/.local/share/ov/custom/x.py")
    assert devwatch.should_watch("/home/ahmed/.config/ov/mod/y.xml")


def test_watch_roots(tmp_path):
    base = tmp_path / "i"
    (base / "custom_addons").mkdir(parents=True)
    (base / "community").mkdir(parents=True)
    inst = _inst(path=str(base),
                 custom_addons_path=str(base / "custom_addons"),
                 community_path=str(base / "community"),
                 enterprise_path="/nonexistent-ent")
    roots = devwatch.watch_roots(inst)
    assert str(base / "custom_addons") in roots
    assert str(base / "community") in roots
    assert "/nonexistent-ent" not in roots


# ------------------------------------------------------------------ run tests
def test_run_tests_command_and_primary_refusal(tmp_path, db, monkeypatch):
    import odoo_vite.core.proc as proc
    from odoo_vite.core import module_manager as mm

    base = tmp_path / "i"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "odoo.conf").touch()
    inst = _inst(path=str(base), venv_path=str(base / "venv"),
                 community_path=str(base / "community"),
                 conf_path=str(base / "odoo.conf"))
    assert create_instance(inst, db).ok
    from odoo_vite.core.registry import get_instance

    inst = get_instance(inst.id, db)

    # primary refused outright
    res = mm.run_module_tests(inst, "s_db", "sale", db_path=db)
    assert not res.ok and "PRIMARY" in res.message

    calls = []

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0,
              cwd=None, env=None, stdin_text=None):
        calls.append(cmd)
        if progress_cb:
            progress_cb("Ran 3 tests in 1.2s")
            progress_cb("OK")
        from odoo_vite.core.result import Result

        return Result.success(data={"lines": ["Ran 3 tests in 1.2s", "OK"],
                                    "returncode": 0, "cancelled": False})

    monkeypatch.setattr(proc, "run_streaming", _fake)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: DbState(db_name="t", exists=False))
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: None)
    # missing target -> -i path
    res = mm.run_module_tests(inst, "s_test", "sale", db_path=db)
    assert res.ok, res.message
    cmd = calls[0]
    assert "-i" in cmd and "sale" in cmd
    assert "--test-enable" in cmd and "--stop-after-init" in cmd
    assert res.data["summary"]["status"] == "passed"
    assert res.data["summary"]["ran"] == 3

    # existing target -> -u path
    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: DbState(
                            db_name="t", exists=True, initialized=True))
    calls.clear()
    res = mm.run_module_tests(inst, "s_test", "sale", db_path=db)
    assert res.ok
    assert "-u" in calls[0] and "-i" not in calls[0]


def test_parse_test_summary_variants():
    from odoo_vite.core.module_manager import parse_test_summary

    ok = parse_test_summary(["Ran 12 tests in 3.1s", "", "OK"])
    assert (ok["ran"], ok["status"]) == (12, "passed")
    bad = parse_test_summary(["Ran 12 tests", "FAILED (failures=2)"])
    assert (bad["ran"], bad["status"]) == (12, "failed")
    assert parse_test_summary([])["status"] == "unknown"
