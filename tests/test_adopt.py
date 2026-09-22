"""Ticket 4.1 tests: adopt.py parse/validate/detect/adopt (no file writes)."""

import pytest

from odoo_vite.core import adopt
from odoo_vite.core.registry import get_instance_by_name

FULL_CONF = """[options]
addons_path = /srv/odoo/addons,/srv/odoo/enterprise,/srv/custom
db_host = localhost
db_user = odoo
db_password = s3cret
xmlrpc_port = 8070
logfile = /var/log/odoo/odoo.log
"""

MISSING_CONF = """[options]
db_host = localhost
"""


@pytest.fixture
def existing(tmp_path):
    root = tmp_path / "existing"
    (root / "community" / "odoo").mkdir(parents=True)
    (root / "community" / "odoo-bin").touch()
    (root / "community" / "odoo" / "release.py").write_text(
        'version_info = (17, 0, 0, "final", 0, "")\n')
    (root / "odoo.conf").write_text(FULL_CONF)
    return root


def test_parse_full_conf(existing):
    parsed = adopt.parse_conf(existing / "odoo.conf")
    assert parsed["db_user"] == "odoo"
    assert parsed["xmlrpc_port"] == "8070"
    assert parsed["addons_path"].split(",")[1].endswith("enterprise")


def test_parse_missing_file_is_empty_dict(tmp_path):
    assert adopt.parse_conf(tmp_path / "nope.conf") == {}


def test_validate_all_present(existing):
    report = adopt.validate_adopted_conf(adopt.parse_conf(existing / "odoo.conf"))
    assert report == {"addons_path": "present", "db_user": "present",
                      "db_password": "present", "port": "present",
                      "logfile": "present"}


def test_validate_missing_fields(tmp_path):
    conf = tmp_path / "t.conf"
    conf.write_text(MISSING_CONF)
    report = adopt.validate_adopted_conf(adopt.parse_conf(conf))
    assert report["addons_path"] == "missing"
    assert report["db_user"] == "missing"
    assert report["db_password"] == "missing"  # key absent entirely
    assert report["port"] == "missing"
    assert report["logfile"] == "missing"


def test_blank_password_counts_as_present():
    report = adopt.validate_adopted_conf({"db_password": ""})
    assert report["db_password"] == "present"


def test_http_port_counts_for_port():
    assert adopt.validate_adopted_conf({"http_port": "8080"})["port"] == "present"


def test_detect_version(existing):
    assert adopt.detect_version(existing / "community") == "17.0"
    assert adopt.detect_version(existing / "nowhere") == ""


def test_split_addons():
    parts = adopt.split_addons("/srv/odoo/addons,/srv/odoo/enterprise,/srv/custom")
    assert parts["community_addons"] == "/srv/odoo/addons"
    assert parts["enterprise"] == "/srv/odoo/enterprise"
    assert parts["custom"] == "/srv/custom"


def test_adopt_registers_without_touching_files(tmp_path, existing, monkeypatch):
    import odoo_vite.core.provisioning as provisioning
    from odoo_vite.core import git_manager, venv_manager

    def _boom(*a, **k):
        raise AssertionError("file-writing path must never run during adopt")

    monkeypatch.setattr(provisioning, "provision_instance", _boom)
    monkeypatch.setattr(git_manager, "clone_instance", _boom)
    monkeypatch.setattr(venv_manager, "create_venv", _boom)

    db = tmp_path / "reg.db"
    before = {p.name for p in (existing / "community").iterdir()}
    res = adopt.adopt_instance(
        "Adopted One", existing / "odoo.conf", existing / "community",
        overrides={"primary_db": "adopted_db"}, db_path=db)
    assert res.ok, res.message
    assert {p.name for p in (existing / "community").iterdir()} == before

    inst = get_instance_by_name("Adopted One", db)
    assert inst is not None
    assert inst.mode == "adopted"
    assert inst.version == "17.0"
    assert inst.primary_db == "adopted_db"
    assert inst.tracked_dbs == ["adopted_db"]
    assert inst.path == str(existing)  # parent of conf inferred
    assert inst.status in ("stopped", "running")


def test_adopt_name_uniqueness_and_required_db(tmp_path, existing):
    db = tmp_path / "reg.db"
    ok = adopt.adopt_instance("Dup", existing / "odoo.conf",
                              existing / "community",
                              overrides={"primary_db": "d1"}, db_path=db)
    assert ok.ok
    dup = adopt.adopt_instance("Dup", existing / "odoo.conf",
                               existing / "community",
                               overrides={"primary_db": "d2"}, db_path=db)
    assert not dup.ok and "already exists" in dup.message
    nodb = adopt.adopt_instance("NoDB", existing / "odoo.conf",
                                existing / "community", overrides={},
                                db_path=db)
    assert not nodb.ok and "Primary database is required" in nodb.message


def test_adopt_overrides_fill_gaps(tmp_path):
    root = tmp_path / "gap"
    (root / "community").mkdir(parents=True)
    (root / "community" / "odoo-bin").touch()
    conf = root / "odoo.conf"
    conf.write_text(MISSING_CONF)
    db = tmp_path / "reg.db"
    res = adopt.adopt_instance(
        "Gaps", conf, root / "community",
        overrides={"primary_db": "g_db", "db_user": "odoo",
                   "port": 8081, "logfile": "/tmp/x.log",
                   "path": str(root)},
        db_path=db)
    assert res.ok, res.message
    inst = get_instance_by_name("Gaps", db)
    assert inst.port == 8081 and inst.db_user == "odoo"
    assert inst.log_path == "/tmp/x.log" and inst.path == str(root)
