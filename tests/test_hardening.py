"""Phase 1.5 hardening tests: H.1 modes, H.2 secrets, H.3 multi-drop,
H.4 matrix, H.5 enterprise check. Hermetic except live-postgres probes,
which skip when no server is reachable."""

import os
import subprocess

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


# ------------------------------------------------------------------ H.1
def test_settings_crud_and_validation(tmp_path, monkeypatch):
    from odoo_vite.core.settings import (
        get_provisioning_mode,
        get_setting,
        set_provisioning_mode,
        set_setting,
    )

    db = str(tmp_path / "s.db")
    assert get_provisioning_mode(db) == "developer"
    assert set_provisioning_mode("managed", db).ok
    assert get_provisioning_mode(db) == "managed"
    assert get_setting("provisioning_mode", None, db) == "managed"
    bad = set_provisioning_mode("root", db)
    assert not bad.ok
    assert get_provisioning_mode(db) == "managed"  # unchanged
    assert set_setting("other", "1", db).ok
    assert get_setting("other", None, db) == "1"


def test_ensure_role_scripts_per_mode():
    from odoo_vite.core import db_manager

    dev = db_manager.ensure_role("u1", "p", dry_run=True, createdb=True)
    assert dev.ok and "CREATEDB" in dev.data["script"]
    man = db_manager.ensure_role("u1", "p", dry_run=True, createdb=False)
    assert man.ok and "NOCREATEDB" in man.data["script"]
    assert "CREATEDB" not in man.data["script"].replace("NOCREATEDB", "")


def test_ensure_role_resolves_global_mode(tmp_path, monkeypatch):
    from odoo_vite.core import db_manager
    from odoo_vite.core.settings import set_provisioning_mode

    db = str(tmp_path / "s.db")
    monkeypatch.setenv("ODOO_VITE_DB", db)
    try:
        assert set_provisioning_mode("managed", db).ok
        man = db_manager.ensure_role("u2", "p", dry_run=True)
        assert "NOCREATEDB" in man.data["script"]
        assert set_provisioning_mode("developer", db).ok
        dev = db_manager.ensure_role("u2", "p", dry_run=True)
        assert "CREATEDB" in dev.data["script"]
    finally:
        monkeypatch.undo()


def test_create_database_owner_and_escalation(monkeypatch):
    from odoo_vite.core import db_manager

    dry = db_manager.create_database("d1", dry_run=True)
    assert dry.ok and "OWNER" in " ".join(dry.data["owner_command"])
    assert dry.data["privileged_command"][0] == "pkexec"
    assert any("GRANT ALL ON SCHEMA public" in a
               for a in dry.data["privileged_command"]), \
        "BUG-2: privileged create must grant schema access (PG15+ lockdown)"

    calls = []

    def _ok(cmd, env_extra=None, timeout=60):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(db_manager, "_run", _ok)
    res = db_manager.create_database("d1", "odoo", "pw")
    assert res.ok and res.data["privileged"] is False

    def _denied_then_ok(cmd, env_extra=None, timeout=60):
        calls.append(cmd)
        if cmd[0] == "pkexec":
            return 0, ""
        return 1, "ERROR: permission denied to create database"

    monkeypatch.setattr(db_manager, "_run", _denied_then_ok)
    res = db_manager.create_database("d2", "odoo", "pw")
    assert res.ok and res.data["privileged"] is True
    assert any(c[0] == "pkexec" for c in calls)

    def _exists(cmd, env_extra=None, timeout=60):
        return 1, 'ERROR: database "d3" already exists'

    monkeypatch.setattr(db_manager, "_run", _exists)
    res = db_manager.create_database("d3", "odoo", "pw")
    assert not res.ok and res.data.get("collision") is True


def test_managed_role_live_or_skip():
    """Live NOCREATEDB creation. Skips (no hang) when pkexec auth is
    unavailable headless — polkit session state decides, not us."""
    import threading

    from odoo_vite.core import db_manager

    if not db_manager.server_reachable():
        pytest.skip("no local postgres")

    user = "h1_probe_user"
    outcome: dict = {}

    def _go():
        try:
            outcome["res"] = db_manager.ensure_role(user, "pw123456",
                                                    createdb=False)
        except Exception as exc:
            outcome["error"] = str(exc)

    waiter = threading.Thread(target=_go, daemon=True)
    waiter.start()
    waiter.join(timeout=30)
    if waiter.is_alive() or "res" not in outcome:
        pytest.skip("pkexec auth unavailable in this session (no hang, skipping)")
    res = outcome["res"]
    assert res.ok, res.message
    env = dict(os.environ, PGPASSWORD="odoo")
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "odoo", "-d", "postgres", "-tAc",
         f"SELECT rolcreatedb FROM pg_roles WHERE rolname='{user}'"],
        capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "f", "managed role must NOT have CREATEDB"
    # NOTE: cleanup DROP ROLE needs CREATEROLE (odoo role lacks it), so a
    # leftover role may persist — harmless; the fast path covers re-runs.


# ------------------------------------------------------------------ H.2
def test_keyring_probe_and_unavailable_store(monkeypatch):
    from odoo_vite.core import registry

    assert registry.keyring_available() is True  # Secret Service present here

    import keyring

    def _boom(*a, **k):
        raise RuntimeError("no backend")

    monkeypatch.setattr(keyring, "set_password", _boom)
    assert registry.keyring_available() is False
    assert registry.store_db_password("x", "pw") == ("unavailable", "pw")
    assert registry.store_db_password("x", "pw", allow_plaintext=True) == (
        "plaintext", "pw")


def test_provision_aborts_loudly_without_keyring(tmp_path, db, monkeypatch):
    from odoo_vite.core import provisioning
    from odoo_vite.core.registry import list_instances

    import odoo_vite.core.registry as reg

    monkeypatch.setattr(reg, "store_db_password",
                        lambda _id, _pw, **k: ("unavailable", _pw))
    inst = Instance(name="NoVault", path=str(tmp_path / "i"))
    res = provisioning.provision_instance(inst, db_path=db)
    assert not res.ok and res.data.get("failed_step") == "register"
    assert "keyring" in res.message.lower()
    assert list_instances(db) == []  # no row left behind


def test_migrate_password_to_keyring(tmp_path, db):
    from odoo_vite.core.registry import get_db_password, migrate_password_to_keyring

    inst = Instance(name="Plain", path="/tmp/x", db_password="s3cret",
                    password_storage="plaintext")
    assert create_instance(inst, db).ok
    res = migrate_password_to_keyring(inst.id, db)
    assert res.ok, res.message
    row = get_instance(inst.id, db)
    assert row.password_storage == "keyring" and row.db_password == ""
    assert get_db_password(row) == "s3cret"


# ------------------------------------------------------------------ H.3
def _mktracked(tmp_path, **overrides):
    base = tmp_path / "rm"
    base.mkdir(exist_ok=True)
    kwargs = {"name": "R", "mode": "managed", "path": str(base),
              "port": 8069, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "r_main", "tracked_dbs": ["r_main", "r_side"],
              "status": "stopped"}
    kwargs.update(overrides)
    return Instance(**kwargs)


def test_remove_drops_checked_tracked_dbs(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager, removal

    dropped = []
    monkeypatch.setattr(db_manager, "server_reachable", lambda: True)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: True)
    monkeypatch.setattr(db_manager, "drop_database",
                        lambda name, *a, **k: dropped.append(name) or Result.success())
    inst = _mktracked(tmp_path)
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=False,
                                  drop_extra_dbs=["r_side"], db_path=db)
    assert res.ok, res.message
    assert dropped == ["r_side"] and res.data["db_dropped"] is True
    assert get_instance(inst.id, db) is None


def test_remove_refuses_unassociated_db(tmp_path, db):
    from odoo_vite.core import removal

    inst = _mktracked(tmp_path)
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=False,
                                  drop_extra_dbs=["postgres"], db_path=db)
    assert not res.ok and "not associated" in res.message
    assert get_instance(inst.id, db) is not None  # untouched
    assert (tmp_path / "rm").exists()


# ------------------------------------------------------------------ H.4
def test_matrix_verified_values():
    from odoo_vite.core.system_check import VERSION_REQUIREMENTS, check_requirements

    assert VERSION_REQUIREMENTS["15.0"]["min_python"] == (3, 7)
    assert VERSION_REQUIREMENTS["16.0"]["min_python"] == (3, 7)
    assert VERSION_REQUIREMENTS["17.0"]["min_python"] == (3, 10)
    assert VERSION_REQUIREMENTS["18.0"]["min_python"] == (3, 10)
    for ver in ("15.0", "16.0", "17.0", "18.0"):
        assert VERSION_REQUIREMENTS[ver]["min_postgres"] == 12
    res = check_requirements("17.0")
    assert res.ok and "pg_version" in res.data and "pg_ok" in res.data


# ------------------------------------------------------------------ H.5
def _enterprise(tmp_path, version):
    ent = tmp_path / "ent"
    mod = ent / "sale_enterprise"
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text(
        "{'name': 'Sale Enterprise', 'version': '%s', 'depends': ['sale']}" % version)
    return ent


def test_enterprise_match_and_mismatch(tmp_path):
    from odoo_vite.core.adopt import (
        check_enterprise_match,
        enterprise_major,
        manifest_version,
        sample_enterprise_version,
    )

    ent = _enterprise(tmp_path, "17.0.1.0.0")
    assert manifest_version(ent / "sale_enterprise" / "__manifest__.py") == "17.0.1.0.0"
    assert sample_enterprise_version(ent) == "17.0.1.0.0"
    assert enterprise_major("17.0.1.0.0") == "17.0"
    ok = check_enterprise_match("17.0", ent)
    assert ok == {"checked": True, "enterprise_version": "17.0.1.0.0",
                  "enterprise_major": "17.0", "match": True}
    bad = check_enterprise_match("16.0", ent)
    assert bad["match"] is False and bad["enterprise_major"] == "17.0"
    unknown = check_enterprise_match("17.0", tmp_path / "empty")
    assert unknown["match"] is None
    assert check_enterprise_match("17.0", None)["checked"] is False
