"""RC sprint unit tests: BUG-1 start race, #11 NOCASE names, adopt privilege
neutrality, H.1 managed seam. Real SQLite in tmp; Popen/psutil mocked."""

import subprocess
import threading
import time as _time

import pytest

from odoo_vite.core import process_manager
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import (
    create_instance,
    get_instance,
    get_instance_by_name,
)
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("ODOO_VITE_LOCKS", str(tmp_path / "locks"))
    return tmp_path / "reg.db"


def _fs(base):
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").touch()


def _inst(base, **overrides):
    kwargs = {"name": "RC", "version": "17.0", "path": str(base),
              "venv_path": str(base / "venv"),
              "community_path": str(base / "community"),
              "conf_path": str(base / "odoo.conf"),
              "log_path": str(base / "logs" / "odoo.log"),
              "port": 8095, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "rc_db", "status": "stopped",
              "db_created": True}
    kwargs.update(overrides)
    return Instance(**kwargs)


class SlowPopen:
    launched: list = []

    def __init__(self, cmd, **kwargs):
        SlowPopen.launched.append(list(cmd))
        self.cmd = cmd
        self.pid = 313371
        self.returncode = 0

    def poll(self):
        return None


def test_bug1_double_start_launches_once(tmp_path, db, monkeypatch):
    """BUG-1: two rapid Starts → exactly one odoo-bin, other refused."""
    monkeypatch.setattr(subprocess, "Popen", SlowPopen)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    from odoo_vite.core import db_manager

    # healthy instance (initialized DB) so both threads reach the launch gate
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: True)
    monkeypatch.setattr(db_manager, "database_initialized",
                        lambda *a, **k: True)
    # shrink the liveness sleep so the test stays fast but racy
    # (bind the original first: process_manager.time IS the time module)
    _real_sleep = _time.sleep
    monkeypatch.setattr(process_manager.time, "sleep",
                        lambda s: _real_sleep(0.3))
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok

    SlowPopen.launched = []
    barrier = threading.Barrier(2)
    results = []

    def _go():
        barrier.wait()
        results.append(process_manager.start_instance(inst.id, db_path=db))

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)
    assert len(SlowPopen.launched) == 1, f"launched {len(SlowPopen.launched)}x!"
    oks = [r.ok for r in results]
    assert oks == [True, False] or oks == [False, True]
    loser = results[0] if not results[0].ok else results[1]
    assert ("already in progress" in loser.message
            or "already running" in loser.message)


def test_names_case_insensitive(tmp_path, db):
    assert create_instance(Instance(name="Test", path="/tmp/a"), db).ok
    dup = create_instance(Instance(name="test", path="/tmp/b"), db)
    assert not dup.ok and "case-insensitive" in dup.message
    assert get_instance_by_name("TEST", db).name == "Test"
    assert get_instance_by_name("tEsT", db) is not None


def test_adopt_never_touches_roles(tmp_path, db, monkeypatch):
    """RC #8 critical: adopt must not create/escalate roles, ever."""
    from odoo_vite.core import adopt, db_manager

    def _boom(*a, **k):
        raise AssertionError("adopt touched role privileges!")

    monkeypatch.setattr(db_manager, "ensure_role", _boom)
    monkeypatch.setattr(db_manager, "create_database", _boom)
    monkeypatch.setattr(db_manager, "drop_database", _boom)

    root = tmp_path / "legacy"
    (root / "community" / "odoo").mkdir(parents=True)
    (root / "community" / "odoo-bin").touch()
    (root / "community" / "odoo" / "release.py").write_text(
        'version_info = (16, 0, 0, "final", 0, "")\n')
    (root / "odoo.conf").write_text(
        "[options]\naddons_path = /srv/a\ndb_user = odoo\nxmlrpc_port = 8070\n")
    res = adopt.adopt_instance("Legacy", root / "odoo.conf",
                               root / "community",
                               overrides={"primary_db": "legacy_db"},
                               db_path=db)
    assert res.ok, res.message
    assert get_instance_by_name("Legacy", db).mode == "adopted"


def test_managed_first_start_uses_explicit_create(tmp_path, db, monkeypatch):
    """H.1 seam: managed + missing DB → create_database THEN -i launch."""
    from odoo_vite.core import db_manager

    monkeypatch.setattr(subprocess, "Popen", SlowPopen)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: False)
    monkeypatch.setattr(db_manager, "database_initialized",
                        lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(
        db_manager, "create_database",
        lambda *a, **k: calls.append("create") or Result.success(message="made"))
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base, db_created=False, provisioning_mode="managed")
    assert create_instance(inst, db).ok

    SlowPopen.launched = []
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert res.ok, res.message
    assert calls == ["create"]
    cmd = SlowPopen.launched[0]
    assert "-i" in cmd  # explicit create first, -i init second


def test_managed_create_failure_aborts_before_launch(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager

    monkeypatch.setattr(subprocess, "Popen", SlowPopen)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: False)
    monkeypatch.setattr(db_manager, "database_initialized",
                        lambda *a, **k: False)
    monkeypatch.setattr(db_manager, "create_database",
                        lambda *a, **k: Result.failure("pkexec dismissed"))
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base, db_created=False, provisioning_mode="managed")
    assert create_instance(inst, db).ok

    SlowPopen.launched = []
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: True, db_path=db)
    assert not res.ok and "Managed mode" in res.message
    assert SlowPopen.launched == []
    assert get_instance(inst.id, db).status == "stopped"


def test_enospc_during_clone_leaves_resumable_draft(tmp_path, db, monkeypatch):
    """RC #14 seam: disk-full mid-clone → draft + last_error, nothing orphaned."""
    from odoo_vite.core import git_manager, provisioning
    from odoo_vite.core.registry import get_instance, list_instances
    from odoo_vite.core.result import Result as R

    import odoo_vite.core.registry as reg

    monkeypatch.setattr(reg, "store_db_password",
                        lambda _id, _pw, **k: ("plaintext", _pw))
    monkeypatch.setattr(
        git_manager, "clone_instance",
        lambda *a, **k: R.failure(
            "git clone of Odoo 17.0 failed: [Errno 28] No space left on device",
            data={"community_path": str(tmp_path / "i" / "community")}))

    base = tmp_path / "i"
    inst = Instance(name="Full", version="17.0", path=str(base),
                    db_password="x", primary_db="full_db", status="draft")
    res = provisioning.provision_instance(inst, db_path=db)
    assert not res.ok and res.data.get("failed_step") == "clone"
    assert "No space left" in res.message
    row = get_instance(inst.id, db)
    assert row is not None and row.status == "draft"  # trace kept, resumable
    assert row.last_error and "No space left" in row.last_error
    # Discard cleans everything: no orphaned row, no orphaned folder.
    (base / "community").mkdir(parents=True)  # partial clone remnant
    assert provisioning.discard_draft(inst.id, db).ok
    assert list_instances(db) == []
    assert not base.exists()
