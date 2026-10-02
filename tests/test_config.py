"""Sprint 7 tests: shared parser, validated writes, addon paths, metadata."""

import configparser

import pytest

from odoo_vite.core import addon_paths, conf_manager
from odoo_vite.core.instance import Instance, effective_python
from odoo_vite.core.registry import create_instance, get_instance


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


CONF = """[options]
addons_path = /a/addons,/b/ent
db_host = localhost
db_user = odoo
xmlrpc_port = 8069

[queue_job]
channels = root:1
"""


@pytest.fixture
def conf(tmp_path):
    path = tmp_path / "odoo.conf"
    path.write_text(CONF)
    return path


# ------------------------------------------------------- parser + read
def test_parser_round_trip_and_sections(conf):
    parsed = conf_manager.parse_conf_file(conf)
    assert parsed["options"]["db_user"] == "odoo"
    assert parsed["queue_job"]["channels"] == "root:1"
    # adopt's parser is the same shared implementation
    from odoo_vite.core.adopt import parse_conf

    assert parse_conf(conf)["xmlrpc_port"] == "8069"
    assert conf_manager.parse_conf_file(conf / "missing") == {}


def test_read_conf(conf):
    res = conf_manager.read_conf(conf)
    assert res.ok and res.data["options"]["db_host"] == "localhost"
    assert "queue_job" in res.data["sections"]
    assert not conf_manager.read_conf(conf.parent / "nope.conf").ok


# ------------------------------------------------------- validated write
def test_update_round_trip_preserves_sections(conf):
    res = conf_manager.update_conf_keys(conf, {"xmlrpc_port": "8071"})
    assert res.ok, res.message
    text = conf.read_text()
    assert "xmlrpc_port = 8071" in text
    assert "[queue_job]" in text and "channels = root:1" in text
    assert (conf.parent / "odoo.conf.bak").is_file()
    # backup holds the previous value
    assert "xmlrpc_port = 8069" in (conf.parent / "odoo.conf.bak").read_text()


def test_update_rejects_bad_writes(conf):
    before = conf.read_text()
    assert not conf_manager.update_conf_keys(conf, {"xmlrpc_port": ""}).ok
    assert not conf_manager.update_conf_keys(conf, {"xmlrpc_port": "abc"}).ok
    assert not conf_manager.update_conf_keys(conf, {"xmlrpc_port": "99999"}).ok
    assert not conf_manager.update_conf_keys(conf, {"workers": "-1"}).ok
    assert not conf_manager.update_conf_keys(conf, {}).ok
    assert conf.read_text() == before, "refused writes must not touch the file"
    # deletion of an unmanaged key is allowed
    assert conf_manager.update_conf_keys(conf, {"some_custom_flag": "1"}).ok
    assert conf_manager.update_conf_keys(conf, {"some_custom_flag": None}).ok
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    parser.read(str(conf))
    assert not parser.has_option("options", "some_custom_flag")


def test_restore_backup(conf):
    assert conf_manager.update_conf_keys(conf, {"db_user": "someone"}).ok
    res = conf_manager.restore_conf_backup(conf)
    assert res.ok, res.message
    assert "db_user = odoo" in conf.read_text()
    assert conf_manager.conf_backup_info(conf)["size"] > 0


def test_regenerate_preserves_extra_sections(tmp_path, db):

    base = tmp_path / "i"
    inst = Instance(name="R", version="17.0", path=str(base),
                    venv_path=str(base / "venv"),
                    community_path=str(base / "community"),
                    custom_addons_path=str(base / "custom_addons"),
                    conf_path=str(base / "odoo.conf"),
                    log_path=str(base / "logs" / "odoo.log"),
                    port=8069, db_user="odoo", db_password="pw",
                    primary_db="r_db")
    assert create_instance(inst, db).ok
    (base / "community" / "addons").mkdir(parents=True)
    conf = base / "odoo.conf"
    conf.write_text(CONF)  # includes [queue_job]
    res = conf_manager.regenerate_conf(inst)
    assert res.ok, res.message
    assert "queue_job" in res.data.get("preserved_sections", [])
    text = conf.read_text()
    assert "[queue_job]" in text and "[options]" in text
    assert "db_user = odoo" in text


# ------------------------------------------------------- addon paths
def test_derive_and_migrate():
    assert addon_paths.derive_addons_path([
        {"path": "/a", "enabled": True},
        {"path": "/b", "enabled": False},
        {"path": "/c", "enabled": True},
    ]) == "/a,/c"
    assert addon_paths.parse_addons_string("/a, /b,,") == [
        {"path": "/a", "enabled": True}, {"path": "/b", "enabled": True}]
    assert addon_paths.derive_addons_path([]) == ""


def test_count_addon_modules(tmp_path):
    root = tmp_path / "addons"
    (root / "m1").mkdir(parents=True)
    (root / "m1" / "__manifest__.py").write_text("{}")
    (root / "m2").mkdir()
    (root / "m2" / "__manifest__.py").write_text("{}")
    (root / "not_a_module").mkdir()
    (root / "loose.py").write_text("")
    assert addon_paths.count_addon_modules(str(root)) == 2
    assert addon_paths.count_addon_modules(str(tmp_path / "gone")) == 0
    assert addon_paths.count_addon_modules(str(root / "m1")) == 0


def test_apply_writes_conf_then_registry(tmp_path, db):
    base = tmp_path / "i"
    (base / "custom_addons").mkdir(parents=True)
    inst = Instance(name="A", path=str(base),
                    conf_path=str(base / "odoo.conf"),
                    custom_addons_path=str(base / "custom_addons"),
                    port=8069, db_user="odoo", primary_db="a_db")
    (base / "odoo.conf").write_text("[options]\naddons_path = /a\n")
    assert create_instance(inst, db).ok
    entries = [{"path": "/a", "enabled": True},
               {"path": "/b", "enabled": False}]
    res = addon_paths.apply_addons_state(inst.id, entries, db_path=db)
    assert res.ok, res.message
    assert res.data["addons_path"] == "/a"
    assert get_instance(inst.id, db).addons_state == entries
    text = (base / "odoo.conf").read_text()
    assert "addons_path = /a\n" in text and "/b" not in text
    # empty path / empty result refused
    assert not addon_paths.apply_addons_state(
        inst.id, [{"path": "", "enabled": True}], db_path=db).ok
    assert not addon_paths.apply_addons_state(
        inst.id, [{"path": "/b", "enabled": False}], db_path=db).ok


def test_ensure_migrates_from_conf_string(tmp_path, db):
    base = tmp_path / "i"
    base.mkdir(parents=True)
    inst = Instance(name="M", path=str(base),
                    conf_path=str(base / "odoo.conf"))
    (base / "odoo.conf").write_text("[options]\naddons_path = /x,/y\n")
    assert create_instance(inst, db).ok
    fresh = get_instance(inst.id, db)
    assert fresh.addons_state == []  # not yet migrated
    migrated = addon_paths.get_addons_state(fresh)
    assert [e["path"] for e in migrated] == ["/x", "/y"]
    assert all(e["enabled"] for e in migrated)


# ------------------------------------------------------- metadata
def test_metadata_round_trip_and_migration(tmp_path, db):
    inst = Instance(name="Meta", description="client A", workers=2,
                    log_level="debug", python_binary="/usr/bin/python3")
    assert create_instance(inst, db).ok
    row = get_instance(inst.id, db)
    assert (row.description, row.workers, row.log_level,
            row.python_binary) == ("client A", 2, "debug", "/usr/bin/python3")
    legacy = Instance(name="Legacy")
    assert create_instance(legacy, db).ok
    old = get_instance(legacy.id, db)
    assert (old.description, old.workers, old.log_level,
            old.python_binary) == ("", 0, "info", "")


def test_effective_python():
    assert effective_python(Instance(
        name="a", venv_path="/v", python_binary="/usr/bin/python3.11")) == \
        "/usr/bin/python3.11"
    assert effective_python(Instance(name="b", venv_path="/v")) == "/v/bin/python"
    assert effective_python(Instance(name="c")) == ""
