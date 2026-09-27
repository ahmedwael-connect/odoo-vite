"""U5.1: instance cloning — files + registry row, no DB copy."""

import configparser

import pytest

from odoo_vite.core.clone import clone_instance
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import (
    create_instance,
    get_db_password,
    get_instance,
    store_db_password,
)


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "clone.db"))
    return tmp_path


def _make_source(tmp_path, name="Orig", status="stopped"):
    from odoo_vite.core import provisioning as prov

    base = tmp_path / "src-tree"
    (base / "community" / "addons").mkdir(parents=True)
    (base / "community" / "odoo-bin").write_text("#!/bin/sh\n")
    mod = base / "custom_addons" / "my_mod"
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'x'}\n")
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").write_text("stub\n")
    (base / "logs").mkdir(parents=True)
    (base / "logs" / "odoo.log").write_text("old log\n")
    (base / "odoo.conf").write_text("[options]\nxmlrpc_port = 8069\n")
    inst = Instance(
        name=name, version="17.0", mode="managed", path=str(base),
        venv_path=str(base / "venv"),
        community_path=str(base / "community"),
        custom_addons_path=str(base / "custom_addons"),
        conf_path=str(base / "odoo.conf"),
        log_path=str(base / "logs" / "odoo.log"),
        port=prov.suggest_port(8070), db_user="odoo",
        primary_db="origdb", tracked_dbs=["origdb"], status=status,
        provisioning_mode="developer",
    )
    storage, column = store_db_password(
        inst.id, "secret", allow_plaintext=True)
    inst.password_storage = storage
    inst.db_password = column
    assert create_instance(inst).ok, "source fixture must register"
    return inst


def _conf_port(conf_path):
    parser = configparser.ConfigParser()
    parser.read(conf_path)
    return parser["options"]["xmlrpc_port"]


def test_clone_happy_path(_env):
    tmp_path = _env
    src = _make_source(tmp_path)
    res = clone_instance(src.id, "Copy", db_path=None)
    assert res.ok, res.message
    assert res.data["id"] != src.id
    assert res.data["port"] != src.port

    new = get_instance(res.data["id"])
    assert new is not None
    assert new.name == "Copy"
    assert new.status == "stopped"
    assert new.pid is None
    assert new.primary_db == "" and new.tracked_dbs == []
    assert new.db_created is False
    assert get_db_password(new) == "secret"

    from pathlib import Path
    dest = Path(res.data["path"])
    assert (dest / "custom_addons" / "my_mod" / "__manifest__.py").exists()
    assert (dest / "community" / "odoo-bin").exists()
    assert not (dest / "venv").exists(), "venv must not be copied"
    assert (dest / "logs").is_dir()
    old_log = dest / "logs" / "odoo.log"
    assert not old_log.exists() or old_log.read_text() == "", \
        "old log content must not be copied (fresh file only)"
    assert _conf_port(new.conf_path) == str(new.port)
    # source untouched
    assert get_instance(src.id).primary_db == "origdb"


def test_clone_refuses_running(_env):
    tmp_path = _env
    src = _make_source(tmp_path, status="running")
    res = clone_instance(src.id, "Copy2")
    assert not res.ok
    assert "Stop" in res.message


def test_clone_duplicate_name_and_missing(_env):
    tmp_path = _env
    src = _make_source(tmp_path)
    assert not clone_instance(src.id, "ORIG").ok  # case-insensitive
    assert not clone_instance("no-such-id", "Whatever").ok
    assert not clone_instance(src.id, "   ").ok
