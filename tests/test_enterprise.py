"""Sprint ENT (core): detect tri-state, load/unload with fakes + tmp dirs."""

import types


from odoo_vite.core import enterprise as ent


def _manifest(root, addon, version="17.0.1.0.0"):
    d = root / addon
    d.mkdir(parents=True)
    (d / "__manifest__.py").write_text(f"{{'name': '{addon}', 'version': '{version}'}}")


def _inst(**kw):
    base = {"enterprise_path": "", "version": "17.0"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_detect_community_when_unset():
    res = ent.detect_enterprise(_inst())
    assert res.ok and res.data["state"] == "community"


def test_detect_valid(tmp_path):
    ent_dir = tmp_path / "enterprise"
    _manifest(ent_dir, "sale_subscription")
    res = ent.detect_enterprise(
        _inst(enterprise_path=str(ent_dir), version="17.0"))
    assert res.ok, res.message
    assert res.data["state"] == "valid"
    assert res.data["enterprise_major"] == "17.0"
    assert res.data["match"] is True


def test_detect_mismatch_still_valid(tmp_path):
    ent_dir = tmp_path / "enterprise"
    _manifest(ent_dir, "sale_subscription", version="16.0.1.0.0")
    res = ent.detect_enterprise(
        _inst(enterprise_path=str(ent_dir), version="17.0"))
    assert res.ok
    assert res.data["state"] == "valid"
    assert res.data["match"] is False


def test_detect_invalid_path(tmp_path):
    res = ent.detect_enterprise(
        _inst(enterprise_path=str(tmp_path / "empty")))
    assert res.ok
    assert res.data["state"] == "invalid"


def test_unload_disables_not_deletes(tmp_path):
    from odoo_vite.core.registry import create_instance
    from odoo_vite.core.instance import Instance

    ent_dir = tmp_path / "inst" / "enterprise"
    _manifest(ent_dir, "crm")
    inst_path = tmp_path / "inst"
    (inst_path / "odoo.conf").write_text(
        "[options]\naddons_path = /srv/addons\n")
    inst = Instance(name="EntUnload", version="17.0", path=str(inst_path),
                    port=8071, primary_db="d",
                    conf_path=str(inst_path / "odoo.conf"),
                    enterprise_path=str(ent_dir))
    db = tmp_path / "reg.db"
    assert create_instance(inst, db_path=db).ok
    from odoo_vite.core import addon_paths
    assert addon_paths.apply_addons_state(
        inst.id, [{"path": "/srv/addons", "enabled": True},
                  {"path": str(ent_dir), "enabled": True}],
        db_path=db).ok

    res = ent.unload_enterprise(inst.id, db_path=db)
    assert res.ok, res.message
    # Entry disabled in place (position kept for re-enable)...
    entries = addon_paths.get_addons_state(
        __import__("odoo_vite.core.registry", fromlist=["get_instance"])
        .get_instance(inst.id, db_path=db))
    ent_entry = next(e for e in entries if e["path"] == str(ent_dir))
    assert ent_entry["enabled"] is False
    # ...record cleared, files untouched.
    from odoo_vite.core.registry import get_instance
    assert get_instance(inst.id, db_path=db).enterprise_path == ""
    assert (ent_dir / "crm" / "__manifest__.py").is_file()
    assert "not deleted" in res.message


def test_unload_without_enterprise_fails(tmp_path):
    from odoo_vite.core.registry import create_instance
    from odoo_vite.core.instance import Instance

    inst = Instance(name="NoEnt", version="17.0", path=str(tmp_path),
                    port=8072, primary_db="d")
    db = tmp_path / "reg2.db"
    assert create_instance(inst, db_path=db).ok
    assert not ent.unload_enterprise(inst.id, db_path=db).ok


def test_load_rejects_empty_url(tmp_path):
    from odoo_vite.core.registry import create_instance
    from odoo_vite.core.instance import Instance

    inst = Instance(name="LoadNoUrl", version="17.0", path=str(tmp_path),
                    port=8073, primary_db="d")
    db = tmp_path / "reg3.db"
    assert create_instance(inst, db_path=db).ok
    res = ent.load_enterprise(inst.id, "", "17.0", db_path=db)
    assert not res.ok and "licensed remote" in res.message


def test_load_from_local_standin_remote(tmp_path):
    """Stand-in for the licensed remote: a local git repo with one addon."""
    import subprocess

    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance, get_instance

    remote = tmp_path / "remote-ent"
    remote.mkdir()
    _manifest(remote, "standin_addon", version="17.0.2.0.0")
    subprocess.run(["git", "init", "-q", "-b", "17.0", remote],
                   check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(remote), "add", "."], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-qm", "init"],
                   check=True)
    inst_path = tmp_path / "inst2"
    inst_path.mkdir()
    (inst_path / "odoo.conf").write_text("[options]\naddons_path = /srv/a\n")
    inst = Instance(name="LoadOk", version="17.0", path=str(inst_path),
                    port=8074, primary_db="d",
                    conf_path=str(inst_path / "odoo.conf"))
    db = tmp_path / "reg4.db"
    assert create_instance(inst, db_path=db).ok

    res = ent.load_enterprise(inst.id, str(remote), "17.0", db_path=db)
    assert res.ok, res.message
    got = get_instance(inst.id, db_path=db)
    assert got.enterprise_path == str(inst_path / "enterprise")
    assert str(inst_path / "enterprise") in open(
        inst_path / "odoo.conf").read()
    assert (inst_path / "enterprise" / "standin_addon").is_dir()


def test_load_auth_failure_surfaces(tmp_path):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    inst_path = tmp_path / "inst3"
    inst_path.mkdir()
    inst = Instance(name="LoadAuth", version="17.0", path=str(inst_path),
                    port=8075, primary_db="d")
    db = tmp_path / "reg5.db"
    assert create_instance(inst, db_path=db).ok
    res = ent.load_enterprise(
        inst.id, "https://invalid.invalid/nobody/nothing.git", "17.0",
        db_path=db)
    assert not res.ok
    # Surfaced as a git failure, never worked around / never credential UI.
    assert "git clone" in res.message
    assert not (inst_path / "enterprise").exists()
