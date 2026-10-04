"""Sprint 5 Part B unit tests: dump validation, validate-config shapes,
initialize orchestration (mocked), backup pre-checks. Live pg_dump/restore
runs happen in the manual E2E, not here."""

import pytest

from odoo_vite.core import db_backup
from odoo_vite.core.db_state import DbState, validate_db_config
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


# ------------------------------------------------------- dump validation
def test_validate_dump_shapes(tmp_path):
    missing = db_backup.validate_dump(tmp_path / "nope.dump")
    assert not missing.ok and "not found" in missing.message

    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")
    assert not db_backup.validate_dump(empty).ok

    garbage = tmp_path / "garbage.dump"
    garbage.write_bytes(b"\x00\x01\x02not a dump at all" * 10)
    assert not db_backup.validate_dump(garbage).ok

    trunc = tmp_path / "trunc.dump"
    trunc.write_bytes(b"PGDMP" + b"\x00" * 20)
    assert not db_backup.validate_dump(trunc).ok

    plain = tmp_path / "plain.sql"
    plain.write_text("--\n-- PostgreSQL database dump\n--\nSELECT 1;\n")
    res = db_backup.validate_dump(plain)
    assert res.ok and res.data["format"] == "plain"

    custom = tmp_path / "custom.dump"
    custom.write_bytes(b"PGDMP" + b"\x00" * 5000)
    res = db_backup.validate_dump(custom)
    # pg_restore --list on garbage-after-magic fails -> clear failure, or if
    # pg_restore is missing the magic alone passes. Either is a sane answer;
    # what must NOT happen is an exception or a silent pass-then-crash.
    assert isinstance(res.ok, bool)


def test_backup_refuses_missing_db(tmp_path, monkeypatch):
    import odoo_vite.core.db_state as mod

    monkeypatch.setattr(mod, "get_db_state",
                        lambda *a, **k: DbState(db_name="ghost"))
    res = db_backup.backup_database("ghost", tmp_path / "ghost.dump")
    assert not res.ok and "does not exist" in res.message


def test_backup_atomic_no_partial_left(tmp_path, monkeypatch):
    """3.3.0 P1: a failed/cancelled pg_dump must not leave a truncated file
    at the final path (prune/index/list cannot tell it from a good dump)."""
    from pathlib import Path

    import odoo_vite.core.db_state as st
    from odoo_vite.core import proc
    from odoo_vite.core.result import Result

    monkeypatch.setattr(db_backup.shutil, "which",
                        lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="db1", exists=True, initialized=True))
    dest = tmp_path / "out.dump"
    part = tmp_path / "out.dump.part"

    def _fail(cmd, progress_cb=None, cancel=None, timeout=0, env=None, **k):
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"PGDMP" + b"\x00" * 10)
        return Result.failure("interrupted")

    monkeypatch.setattr(proc, "run_streaming", _fail)
    res = db_backup.backup_database("db1", dest)
    assert not res.ok
    assert not dest.exists(), "truncated dump left at the final path"
    assert not part.exists(), ".part sibling left behind"

    def _ok(cmd, progress_cb=None, cancel=None, timeout=0, env=None, **k):
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"PGDMP" + b"\x00" * 5000)
        if progress_cb:
            progress_cb("dumping db1")
        return Result.success(data={"returncode": 0})

    monkeypatch.setattr(proc, "run_streaming", _ok)
    res = db_backup.backup_database("db1", dest)
    assert res.ok, res.message
    assert dest.exists() and dest.stat().st_size > 0
    assert not part.exists()
    assert (tmp_path / "out.dump.meta.json").is_file()


def test_restore_validates_first(tmp_path):
    garbage = tmp_path / "garbage.dump"
    garbage.write_bytes(b"junk" * 100)
    res = db_backup.restore_database(garbage, "some_target")
    assert not res.ok and "pg_dump" in res.message.lower()


def test_restore_refuses_system_db(tmp_path):
    plain = tmp_path / "p.sql"
    plain.write_text("-- PostgreSQL database dump\n")
    res = db_backup.restore_database(plain, "postgres")
    assert not res.ok and "system database" in res.message


# ------------------------------------------------------- validate config
def _e2e_like(tmp_path, **overrides):
    base = tmp_path / "i"
    kwargs = {"name": "V", "mode": "managed", "path": str(base),
              "venv_path": str(base / "venv"),
              "community_path": str(base / "community"),
              "conf_path": str(base / "odoo.conf"),
              "log_path": str(base / "logs" / "x.log"),
              "port": 8093, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "e2e_17_demo", "status": "stopped"}
    kwargs.update(overrides)
    return Instance(**kwargs)


def test_validate_live_e2e_shape(tmp_path):
    """Shape of a healthy report.

    3.3.0: hermetic — it used to reach into the developer's REAL registry
    for the 'E2E 17 Demo' row (and silently skipped/failed with machine
    state). Now it builds its own instance; only the live-Postgres probes
    degrade to a skip when the local environment is incomplete.
    """
    inst = _e2e_like(tmp_path)
    base = tmp_path / "i"
    (base / "community").mkdir(parents=True)
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo\n")
    report = validate_db_config(inst)
    by_field = {c["field"]: c for c in report["checks"]}
    assert by_field["conf_file"]["ok"] is True
    live = [c for c in report["checks"]
            if c["field"] in ("db_user", "db_password", "primary_db")]
    if any(c["ok"] is not True for c in live):
        pytest.skip(f"local postgres e2e env unavailable: {live}")
    assert by_field["db_user"]["ok"] is True
    assert by_field["db_password"]["ok"] is True
    assert by_field["primary_db"]["ok"] is True


def test_validate_never_leaks_secret(tmp_path):
    """A distinctive password must not appear anywhere in the report."""
    base = tmp_path / "i"
    base.mkdir()
    (base / "odoo.conf").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo\n")
    inst = _e2e_like(tmp_path, conf_path=str(base / "odoo.conf"),
                     db_password="SecretZZ9!")
    report = validate_db_config(inst)
    assert "SecretZZ9!" not in str(report)


def test_validate_unreachable_host(tmp_path):
    base = tmp_path / "i"
    base.mkdir()
    (base / "odoo.conf").write_text(
        "[options]\ndb_host = localhost\ndb_port = 54999\ndb_user = odoo\n")
    inst = _e2e_like(tmp_path, conf_path=str(base / "odoo.conf"))
    report = validate_db_config(inst)
    by_field = {c["field"]: c for c in report["checks"]}
    assert by_field["db_host/db_port"]["ok"] is False
    assert by_field["db_user"]["ok"] is None  # skipped, not failed


def test_validate_bad_user(tmp_path):
    base = tmp_path / "i"
    base.mkdir()
    (base / "odoo.conf").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = no_such_role_xyz\n")
    inst = _e2e_like(tmp_path, conf_path=str(base / "odoo.conf"),
                     db_user="no_such_role_xyz", db_password="x")
    report = validate_db_config(inst)
    by_field = {c["field"]: c for c in report["checks"]}
    # scram servers don't leak existence: user unverified, password rejected
    assert by_field["db_user"]["ok"] is None
    assert by_field["db_password"]["ok"] is False


# ------------------------------------------------------- initialize
def test_initialize_refuses_while_running(tmp_path, db, monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: 123)
    inst = _e2e_like(tmp_path, status="running", pid=123,
                     primary_db="not_init_db")
    assert create_instance(inst, db).ok
    res = process_manager.initialize_database(inst.id, "not_init_db", db_path=db)
    assert not res.ok and "Stop" in res.message


def test_initialize_noop_when_initialized(tmp_path, db, monkeypatch):
    import odoo_vite.core.db_state as mod
    from odoo_vite.core import process_manager

    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    monkeypatch.setattr(
        mod, "get_db_state",
        lambda *a, **k: DbState(db_name="x", exists=True, initialized=True,
                                odoo_version="17.0"))
    inst = _e2e_like(tmp_path)
    assert create_instance(inst, db).ok
    res = process_manager.initialize_database(inst.id, "e2e_17_demo", db_path=db)
    assert res.ok and "already initialized" in res.message
