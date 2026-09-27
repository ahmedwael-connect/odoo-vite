"""U5.5: export/import round-trip + bundle safety."""

import configparser
import io
import json
import tarfile

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import (
    create_instance,
    get_db_password,
    get_instance,
    store_db_password,
)
from odoo_vite.core.transfer import (
    export_instance,
    export_preview,
    import_instance,
)


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "transfer.db"))
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path


def _make_source(tmp_path, name="Exp", status="stopped"):
    from odoo_vite.core import provisioning as prov

    slug = "".join(c if c.isalnum() else "_" for c in name)
    base = tmp_path / f"src-tree-{slug}"
    (base / "community" / "addons").mkdir(parents=True)
    (base / "community" / "odoo-bin").write_text("#!/bin/sh\n")
    mod = base / "custom_addons" / "my_mod"
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'x'}\n")
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").write_text("stub\n")
    inst = Instance(
        name=name, version="18.0", mode="managed", path=str(base),
        venv_path=str(base / "venv"),
        community_path=str(base / "community"),
        custom_addons_path=str(base / "custom_addons"),
        conf_path=str(base / "odoo.conf"),
        log_path=str(base / "logs" / "odoo.log"),
        port=prov.suggest_port(8070), db_user="odoo",
        primary_db="expdb", tracked_dbs=["expdb"], status=status,
        description="mover",
    )
    storage, column = store_db_password(
        inst.id, "s3cret", allow_plaintext=True)
    inst.password_storage = storage
    inst.db_password = column
    assert create_instance(inst).ok
    return inst


def _conf_port(conf_path):
    parser = configparser.ConfigParser()
    parser.read(conf_path)
    return parser["options"]["xmlrpc_port"]


def test_export_import_round_trip(_env):
    tmp_path = _env
    src = _make_source(tmp_path)
    bundle = tmp_path / "exp_export.tar.gz"
    exp = export_instance(src.id, bundle, src_password="s3cret")
    assert exp.ok, exp.message
    assert bundle.stat().st_mode & 0o777 == 0o600

    prev = export_preview(bundle)
    assert prev.ok and prev.data["name"] == "Exp"
    assert prev.data["version"] == "18.0"

    imp = import_instance(bundle, "Imp")
    assert imp.ok, imp.message
    new = get_instance(imp.data["id"])
    assert new.name == "Imp"
    assert new.version == "18.0"
    assert new.status == "stopped"
    assert new.primary_db == "" and new.tracked_dbs == []
    assert new.description == "mover"
    assert get_db_password(new) == "s3cret"
    assert _conf_port(new.conf_path) == str(new.port)
    from pathlib import Path
    dest = Path(imp.data["path"])
    assert (dest / "custom_addons" / "my_mod" / "__manifest__.py").exists()
    assert not (dest / "venv").exists()


def test_export_refuses_running_and_bad_dest(_env):
    tmp_path = _env
    src = _make_source(tmp_path, status="running")
    assert not export_instance(src.id, tmp_path / "x.tar.gz").ok
    src2 = _make_source(tmp_path, name="Exp2", status="stopped")
    assert not export_instance(src2.id, tmp_path / "x.zip").ok
    assert not export_instance("no-such-id", tmp_path / "x.tar.gz").ok


def test_import_rejects_traversal_and_format(_env):
    tmp_path = _env
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        payload = json.dumps({"format": 1, "name": "Evil"}).encode()
        info = tarfile.TarInfo("instance.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        bad = tarfile.TarInfo("../../pwned")
        bad.size = 3
        tar.addfile(bad, io.BytesIO(b"xxx"))
    res = import_instance(evil, "Evil")
    assert not res.ok and "unsafe" in res.message

    old = tmp_path / "old.tar.gz"
    with tarfile.open(old, "w:gz") as tar:
        payload = json.dumps({"format": 999, "name": "Old"}).encode()
        info = tarfile.TarInfo("instance.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    res = import_instance(old, "Old")
    assert not res.ok and "Unsupported bundle format" in res.message
    assert not export_preview(old).ok
