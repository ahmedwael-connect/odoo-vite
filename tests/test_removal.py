"""Ticket 4.3 tests: removal.py managed vs adopted semantics."""

import shutil

import pytest

from odoo_vite.core import removal
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance, list_instances
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


def _managed(tmp_path, **overrides):
    base = tmp_path / "managed"
    base.mkdir(exist_ok=True)
    (base / "odoo.conf").touch()
    kwargs = {"name": "M", "version": "17.0", "mode": "managed",
              "path": str(base), "port": 8069, "db_user": "odoo",
              "db_password": "odoo", "primary_db": "m_db",
              "tracked_dbs": ["m_db"], "status": "stopped"}
    kwargs.update(overrides)
    return Instance(**kwargs)


def _adopted(**overrides):
    kwargs = {"name": "A", "version": "16.0", "mode": "adopted",
              "path": "/srv/adopted", "port": 8070, "db_user": "odoo",
              "primary_db": "a_db", "tracked_dbs": ["a_db"],
              "status": "stopped"}
    kwargs.update(overrides)
    return Instance(**kwargs)


def test_managed_remove_deletes_files_and_row(tmp_path, db, monkeypatch):
    inst = _managed(tmp_path)
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=False, db_path=db)
    assert res.ok, res.message
    assert res.data == {"mode": "managed", "files_deleted": True,
                        "db_dropped": False}
    assert not (tmp_path / "managed").exists()
    assert list_instances(db) == []


def test_managed_remove_with_drop_db_order(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager, process_manager

    calls: list = []
    monkeypatch.setattr(process_manager, "stop_instance",
                        lambda *a, **k: calls.append("stop") or Result.success())
    monkeypatch.setattr(db_manager, "server_reachable", lambda: True)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: True)
    monkeypatch.setattr(db_manager, "drop_database",
                        lambda *a, **k: calls.append("drop") or Result.success())
    real_rmtree = shutil.rmtree

    def _rec(path, **kwargs):
        calls.append("rmtree")
        return real_rmtree(path, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", _rec)
    inst = _managed(tmp_path, status="running", pid=999999)
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert res.ok, res.message
    assert calls == ["stop", "drop", "rmtree"], calls
    assert list_instances(db) == []


def test_managed_drop_failure_aborts_before_files(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager
    from odoo_vite.core.result import Result

    monkeypatch.setattr(db_manager, "server_reachable", lambda: True)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: True)
    monkeypatch.setattr(db_manager, "drop_database",
                        lambda *a, **k: Result.failure("drop exploded"))
    inst = _managed(tmp_path)
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert not res.ok and "drop exploded" in res.message
    assert (tmp_path / "managed").exists()  # files intact
    assert get_instance(inst.id, db) is not None  # row intact


def test_adopted_remove_ignores_drop_db_loudly(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager

    def _boom(*a, **k):
        raise AssertionError("adopted removal must not touch files or DBs")

    monkeypatch.setattr(shutil, "rmtree", _boom)
    monkeypatch.setattr(db_manager, "drop_database", _boom)
    inst = _adopted()
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert res.ok, res.message
    assert "ignored" in res.message and "adopted" in res.message
    assert list_instances(db) == []


def test_remove_missing_id(db):
    assert not removal.remove_instance("nope", db_path=db).ok
