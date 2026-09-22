"""Ticket 2.7 test: provisioning orchestration with mocked sub-calls.

No network, no postgres, no GTK. Asserts step order, early-exit failure
handling (draft + last_error + failed_step), and retry/idempotency skips.
"""

import pytest

from odoo_vite.core import provisioning
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import get_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path):
    return tmp_path / "reg.db"


def _inst(tmp_path, **overrides):
    base = tmp_path / "inst"
    kwargs = {
        "name": "E2E-Demo",
        "version": "17.0",
        "path": str(base),
        "port": 8071,
        "db_user": "odoo",
        "db_password": "odoo",
        "primary_db": "e2e_demo",
        "tracked_dbs": ["e2e_demo"],
    }
    kwargs.update(overrides)
    return Instance(**kwargs)


@pytest.fixture
def hermetic_passwords(monkeypatch):
    import odoo_vite.core.registry as reg

    monkeypatch.setattr(reg, "store_db_password",
                        lambda _id, pw, **k: ("plaintext", pw))
    monkeypatch.setattr(
        reg, "get_db_password", lambda inst: inst.db_password
    )


def _ok(**data):
    return Result.success(data=data or {"x": 1}, message="ok")


def _patch_all(monkeypatch, calls, fail_at=None, fail_msg="boom"):
    from odoo_vite.core import conf_writer, db_manager, git_manager, venv_manager

    def _rec(name):
        def _fn(*a, **k):
            calls.append(name)
            if fail_at == name:
                return Result.failure(fail_msg)
            return _ok()
        return _fn

    monkeypatch.setattr(git_manager, "clone_instance", _rec("clone"))
    monkeypatch.setattr(venv_manager, "create_venv", _rec("venv"))
    monkeypatch.setattr(venv_manager, "install_requirements", _rec("pip"))
    monkeypatch.setattr(conf_writer, "write_conf",
                        _rec("conf"))
    monkeypatch.setattr(db_manager, "ensure_role", _rec("role"))


def test_success_order_and_finalize(tmp_path, db, monkeypatch, hermetic_passwords):
    calls: list = []
    _patch_all(monkeypatch, calls)
    inst = _inst(tmp_path)
    logs: list = []
    res = provisioning.provision_instance(inst, progress_cb=logs.append, db_path=db)
    assert res.ok, res.message
    assert calls == ["clone", "venv", "pip", "conf", "role"], calls
    row = get_instance(inst.id, db)
    assert row is not None and row.status == "stopped"
    assert row.last_error is None
    assert any("Cloning" in line for line in logs)


def test_failure_marks_draft_with_error(tmp_path, db, monkeypatch, hermetic_passwords):
    calls: list = []
    _patch_all(monkeypatch, calls, fail_at="pip", fail_msg="pip exploded")
    inst = _inst(tmp_path)
    res = provisioning.provision_instance(inst, db_path=db)
    assert not res.ok
    assert res.data["failed_step"] == "pip"
    assert calls == ["clone", "venv", "pip"], calls  # early exit: no conf/role
    row = get_instance(inst.id, db)
    assert row.status == "draft"
    assert "pip exploded" in (row.last_error or "")


def test_retry_skips_completed_steps(tmp_path, db, monkeypatch, hermetic_passwords):
    inst = _inst(tmp_path)
    base = tmp_path / "inst"
    (base / "community" / ".git").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / provisioning.PIP_DONE_SENTINEL).touch()
    calls: list = []
    _patch_all(monkeypatch, calls)
    res = provisioning.provision_instance(inst, db_path=db)
    assert res.ok, res.message
    assert calls == ["conf", "role"], calls  # clone/venv/pip skipped
    assert get_instance(inst.id, db).status == "stopped"


def test_already_provisioned_is_noop(tmp_path, db, monkeypatch, hermetic_passwords):
    calls: list = []
    _patch_all(monkeypatch, calls)
    inst = _inst(tmp_path)
    assert provisioning.provision_instance(inst, db_path=db).ok
    again = provisioning.provision_instance(inst, db_path=db)
    assert again.ok
    assert calls == ["clone", "venv", "pip", "conf", "role"]  # unchanged


def test_discard_draft(tmp_path, db, hermetic_passwords):
    inst = _inst(tmp_path)
    (tmp_path / "inst" / "community").mkdir(parents=True)
    from odoo_vite.core.registry import create_instance, list_instances

    create_instance(inst, db)
    assert len(list_instances(db)) == 1
    res = provisioning.discard_draft(inst.id, db)
    assert res.ok, res.message
    assert list_instances(db) == []
    assert not (tmp_path / "inst").exists()
