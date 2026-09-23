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


def test_validate_live_e2e_shape():
    """Shape of a healthy report (uses the real E2E row if present)."""
    from odoo_vite.core.registry import get_instance_by_name

    inst = get_instance_by_name("E2E 17 Demo")
    if inst is None:
        pytest.skip("no E2E instance in this registry")
    report = validate_db_config(inst)
    by_field = {c["field"]: c for c in report["checks"]}
    assert by_field["conf_file"]["ok"] is True
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
    base = tmp_path / "i"
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
    base = tmp_path / "i"
    inst = _e2e_like(tmp_path)
    assert create_instance(inst, db).ok
    res = process_manager.initialize_database(inst.id, "e2e_17_demo", db_path=db)
    assert res.ok and "already initialized" in res.message
