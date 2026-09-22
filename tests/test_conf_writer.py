"""Ticket 2.5 test: conf_writer.write_conf() — fully unit-testable.

No GTK, no network. Asserts the ini structure from the Sprint 2 spec §3.3.
"""

import configparser

from odoo_vite.core import conf_writer
from odoo_vite.core.instance import Instance


def _instance(tmp_path, **overrides):
    base = tmp_path / "demo"
    kwargs = {
        "name": "Demo",
        "version": "17.0",
        "path": str(base),
        "venv_path": str(base / "venv"),
        "community_path": str(base / "community"),
        "port": 8069,
        "db_user": "odoo",
        "db_password": "s3cret",
        "password_storage": "plaintext",
        "primary_db": "demo",
    }
    kwargs.update(overrides)
    return Instance(**kwargs)


def test_write_conf_structure(tmp_path):
    inst = _instance(tmp_path)
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message

    data = res.data
    assert data["conf_path"].endswith("odoo.conf")
    assert data["log_path"].endswith("logs/odoo.log")
    assert data["custom_addons_path"].endswith("custom_addons")

    parser = configparser.ConfigParser()
    parser.read(data["conf_path"])
    opts = parser["options"]
    assert opts["db_host"] == "localhost"
    assert opts["db_port"] == "5432"
    assert opts["db_user"] == "odoo"
    assert opts["db_password"] == "s3cret"
    assert opts["xmlrpc_port"] == "8069"
    assert opts["logfile"] == data["log_path"]
    addons = opts["addons_path"].split(",")
    assert addons[0].endswith("community/addons")
    assert addons[-1].endswith("custom_addons")
    assert len(addons) == 2  # no enterprise configured


def test_write_conf_with_enterprise(tmp_path):
    ent = tmp_path / "enterprise"
    ent.mkdir()
    inst = _instance(tmp_path, enterprise_path=str(ent))
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message
    parser = configparser.ConfigParser()
    parser.read(res.data["conf_path"])
    addons = parser["options"]["addons_path"].split(",")
    assert addons[1] == str(ent)
    assert len(addons) == 3


def test_write_conf_creates_folders_and_logfile(tmp_path):
    inst = _instance(tmp_path)
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message
    assert (tmp_path / "demo" / "custom_addons").is_dir()
    assert (tmp_path / "demo" / "logs" / "odoo.log").is_file()


def test_write_conf_keyring_password(tmp_path, monkeypatch):
    import odoo_vite.core.registry as reg

    monkeypatch.setattr(reg, "get_db_password", lambda inst: "from-vault")
    inst = _instance(tmp_path, password_storage="keyring", db_password="")
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message
    parser = configparser.ConfigParser()
    parser.read(res.data["conf_path"])
    assert parser["options"]["db_password"] == "from-vault"


def test_write_conf_no_path_fails():
    inst = Instance(name="NoPath", path="")
    assert not conf_writer.write_conf(inst).ok
