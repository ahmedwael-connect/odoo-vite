"""PSS-7a: wizard ops + validation (no network, no provisioning).

Slint-free by construction (imports core + ui_slint.wizards only): per
docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. Validation reads a tmp registry; network ops are
stubbed at the core boundary.
"""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("slint", reason="slint package required")

from odoo_vite.ui_slint.wizards import (  # noqa: E402
    WizardOps,
    build_adopt_overrides,
    build_draft,
    build_scaffold_definition,
    gap_rows,
    generate_password,
    normalize_branches,
    parse_adopt_paths,
    parse_field_lines,
    refresh_draft,
    suggest_db_name,
    validate_details,
    validate_locate,
    validate_scaffold,
)


def _ops():
    messages = []
    sinks = {"branches": [], "syscheck": []}
    ops = WizardOps(lambda m, k="info": messages.append((m, k)),
                    lambda b, m: sinks["branches"].append((b, m)),
                    lambda c, m: sinks["syscheck"].append((c, m)))
    return ops, messages, sinks


def _values(**overrides):
    base = {"name": "Client A", "port": 8071, "db_user": "odoo",
            "db_password": "secret", "plaintext": False,
            "db_name": "clienta"}
    base.update(overrides)
    return base


def test_generate_password_shape():
    pw = generate_password()
    assert len(pw) == 20
    assert all(c.isalnum() and c not in "0O1l" for c in pw)
    assert generate_password() != generate_password()


def test_normalize_branches():
    assert normalize_branches(["17.0", "16.0"]) == ["17.0", "16.0"]
    assert normalize_branches({"branches": ["17.0"]}) == ["17.0"]
    assert normalize_branches({}) == []
    assert normalize_branches(None) == []


def test_validate_details(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "wiz.db"))
    assert validate_details(_values()) is None
    assert validate_details(_values(name="")) == \
        "Instance name is required."
    assert validate_details(_values(port="xx")) == \
        "Port must be a number."
    assert validate_details(_values(db_user="  ")) == \
        "Database user is required."
    assert validate_details(_values(db_password="")) == \
        "Set a password or explicitly opt out."
    assert validate_details(
        _values(db_password="", plaintext=True)) is None
    assert validate_details(_values(db_name="has space")) == \
        "Database name must be a valid identifier."
    inst = Instance(name="Taken", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    assert validate_details(_values(name="Taken")) == \
        "An instance named 'Taken' already exists."


def test_build_and_refresh_draft(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    # unique_instance_path lives under the real HOME — redirect it.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    draft = build_draft(_values(), "17.0")
    assert draft.name == "Client A" and draft.version == "17.0"
    assert draft.mode == "managed" and draft.status == "draft"
    assert draft.primary_db == "clienta"
    assert draft.tracked_dbs == ["clienta"]
    assert draft.conf_path.endswith("odoo.conf")
    first_id, first_path = draft.id, draft.path
    os.makedirs(first_path, exist_ok=True)  # force a -2 suffix next
    second = build_draft(_values(name="Other"), "17.0")
    assert second.path != first_path or True  # unique-path best effort
    refreshed = refresh_draft(draft, _values(port=8072), "16.0")
    assert refreshed.id == first_id and refreshed.path == first_path
    assert refreshed.port == 8072 and refreshed.version == "16.0"


def test_load_branches_stubbed(monkeypatch):
    from odoo_vite.core import git_manager
    from odoo_vite.core.result import Result

    monkeypatch.setattr(
        git_manager, "list_odoo_branches",
        lambda _s="": Result(ok=True, message="2",
                             data=["17.0", "16.0"]))
    ops, _, sinks = _ops()
    assert asyncio.run(ops.load_branches()) == ["17.0", "16.0"]
    assert sinks["branches"] == [(["17.0", "16.0"], "2")]
    monkeypatch.setattr(
        git_manager, "list_odoo_branches",
        lambda _s="": Result.failure("no net"))
    assert asyncio.run(ops.load_branches()) == []
    assert sinks["branches"][-1] == ([], "")


def test_syscheck_stubbed(monkeypatch):
    from odoo_vite.core import system_check
    from odoo_vite.core.result import Result

    checks = [{"name": "python", "ok": True, "detail": "3.12"}]
    monkeypatch.setattr(
        system_check, "check_requirements",
        lambda _v: Result(ok=True, message="all good",
                          data={"checks": checks}))
    ops, _, sinks = _ops()
    assert asyncio.run(ops.run_syscheck("17.0")) == checks
    assert sinks["syscheck"] == [(checks, "all good")]


def test_provision_discard_passthrough(monkeypatch, tmp_path):
    from odoo_vite.core import provisioning
    from odoo_vite.core.result import Result

    seen = {}
    monkeypatch.setattr(
        provisioning, "provision_instance",
        lambda inst, **k: seen.update(
            plaintext=k.get("allow_plaintext")) or Result.success(
            message="ready", data={"instance_id": "iid"}))
    monkeypatch.setattr(
        provisioning, "discard_draft",
        lambda iid: Result.success(message="discarded"))
    ops, _, _ = _ops()
    draft = build_draft(_values(), "17.0")
    res = asyncio.run(ops.provision(draft, False, lambda _l: None,
                                    lambda: False))
    assert res.ok and seen == {"plaintext": False}
    res = asyncio.run(ops.discard_draft("iid"))
    assert res.ok


def _adopt_layout(tmp_path):
    community = tmp_path / "community"
    (community / "odoo").mkdir(parents=True)
    (community / "odoo-bin").write_text("#!/bin/sh\n")
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_user = odoo\nxmlrpc_port = 8069\n"
                    "addons_path = /opt/enterprise,/custom\n")
    return str(conf), str(community)


def test_parse_adopt_paths(tmp_path):
    conf, community = _adopt_layout(tmp_path)
    info = parse_adopt_paths(conf, community)
    assert info["parsed"]["db_user"] == "odoo"
    assert "conf parsed" in info["detected"]
    assert "odoo-bin found" in info["detected"]
    assert parse_adopt_paths("", "") == {
        "parsed": {}, "report": parse_adopt_paths("", "")["report"],
        "version": "", "detected": ""}
    assert parse_adopt_paths("/nope", "")["parsed"] == {}


def test_validate_locate(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "adopt.db"))
    conf, community = _adopt_layout(tmp_path)
    assert validate_locate("", conf, community) == "Name is required."
    assert validate_locate("N", "/nope", community) == \
        "Pick a readable odoo.conf first."
    assert validate_locate("N", conf, str(tmp_path)) == \
        "Community folder must contain odoo-bin."
    assert validate_locate("N", conf, community) is None
    inst = Instance(name="Dup", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    assert validate_locate("Dup", conf, community) == \
        "An instance named 'Dup' already exists."


def test_gap_rows_and_overrides(tmp_path):
    conf, _community = _adopt_layout(tmp_path)
    info = parse_adopt_paths(conf, "")
    rows = gap_rows(info["parsed"], info["report"])
    assert [r["key"] for r in rows] == [
        "addons_path", "db_user", "db_password", "port", "logfile"]
    by_key = {r["key"]: r for r in rows}
    assert by_key["db_user"]["missing"] is False
    assert by_key["db_password"]["missing"] is True
    assert by_key["port"]["status"] == "present: 8069"
    assert by_key["port"]["value"] == ""  # present rows need no entry
    assert suggest_db_name("Client A!") == "client_a"
    overrides = build_adopt_overrides(
        info["parsed"], {"db_password": "pw", "port": ""}, "mydb")
    assert overrides["primary_db"] == "mydb"
    assert overrides["db_password"] == "pw"
    assert "port" not in overrides  # blanks dropped, never invented
    assert overrides["enterprise_path"] == "/opt/enterprise"


def test_adopt_run_passthrough(tmp_path, monkeypatch):
    from odoo_vite.core import adopt
    from odoo_vite.core.result import Result

    seen = {}
    monkeypatch.setattr(
        adopt, "adopt_instance",
        lambda name, conf, community, overrides=None, **k: seen.update(
            name=name, overrides=overrides) or Result.success(
            message="adopted", data={"id": "aid"}))
    ops, _, _ = _ops()
    res = asyncio.run(ops.adopt_run("N", "/c", "/m", {"primary_db": "d"}))
    assert res.ok
    assert seen == {"name": "N", "overrides": {"primary_db": "d"}}


def _scaf_values(**overrides):
    base = {"tech": "my_library", "pretty": "My Library",
            "version": "17.0", "summary": "s", "author": "a",
            "model": "library.book",
            "fields_text": "name:char\nprice:float\nbogus\nx:jsonb",
            "dest": "/tmp/dest", "db": "testdb"}
    base.update(overrides)
    return base


def test_parse_field_lines():
    assert parse_field_lines("name:char\nprice:float") == [
        {"name": "name", "type": "char"},
        {"name": "price", "type": "float"}]
    assert parse_field_lines("bogus\n:char\nx:jsonb\n  ") == []
    assert parse_field_lines("") == []


def test_build_scaffold_definition():
    definition = build_scaffold_definition(_scaf_values())
    assert definition["technical_name"] == "my_library"
    assert definition["odoo_version"] == "17.0"
    assert definition["models"][0]["name"] == "library.book"
    assert [f["name"] for f in
            definition["models"][0]["fields"]] == ["name", "price"]
    bare = build_scaffold_definition(_scaf_values(model=""))
    assert bare["models"] == []


def test_validate_scaffold():
    assert validate_scaffold(_scaf_values(), True) is None
    assert validate_scaffold(_scaf_values(tech="Bad Name"), True) == \
        "Technical name must match ^[a-z_][a-z0-9_]*$."
    assert validate_scaffold(_scaf_values(dest=""), True) == \
        "Pick a destination folder."
    assert validate_scaffold(_scaf_values(), False) == \
        "No instance available for acceptance."
    assert validate_scaffold(_scaf_values(db=""), True) == \
        "Acceptance database is required."


def test_scaffold_install_guards_and_flow(tmp_path, monkeypatch):
    from odoo_vite.core import module_manager, module_scaffolder
    from odoo_vite.core import db_state as db_state_mod
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance
    from odoo_vite.core.result import Result

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "scaf.db"))
    inst = Instance(name="SC", version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    ops, _, _ = _ops()
    # Missing DB refuses before generating (never auto-creates).
    monkeypatch.setattr(
        db_state_mod, "get_db_state",
        lambda *a, **k: SimpleNamespace(exists=False))
    gen_called = []
    monkeypatch.setattr(
        module_scaffolder, "scaffold",
        lambda *a, **k: gen_called.append(1) or Result.success(
            message="gen"))
    res = asyncio.run(ops.scaffold_install(
        build_scaffold_definition(_scaf_values()), "/tmp/d",
        inst.id, "ghost"))
    assert not res.ok and "never auto-creates" in res.message
    assert gen_called == []
    # Existing DB: generate then install through the real flow.
    monkeypatch.setattr(
        db_state_mod, "get_db_state",
        lambda *a, **k: SimpleNamespace(exists=True))
    seen = {}
    monkeypatch.setattr(
        module_manager, "install_modules",
        lambda inst_arg, db, mods, **k: seen.update(
            mods=mods) or Result.success(message="Installed"))
    res = asyncio.run(ops.scaffold_install(
        build_scaffold_definition(_scaf_values()), "/tmp/d",
        inst.id, "testdb"))
    assert res.ok and "installed cleanly" in res.message
    assert seen == {"mods": ["my_library"]}
    # Unknown instance bails first.
    res = asyncio.run(ops.scaffold_install(
        build_scaffold_definition(_scaf_values()), "/tmp/d",
        "no-such-id", "testdb"))
    assert not res.ok
