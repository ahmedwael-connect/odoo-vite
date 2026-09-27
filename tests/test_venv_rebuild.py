"""U5.2: venv rebuild orchestration (stubbed pip — no network)."""

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance
from odoo_vite.core.result import Result
from odoo_vite.core import venv_manager


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "venv.db"))
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path


def _make_instance(tmp_path, status="stopped"):
    base = tmp_path / "inst"
    (base / "community").mkdir(parents=True)
    (base / "community" / "requirements.txt").write_text("requests\n")
    stale = base / "venv"
    (stale / "bin").mkdir(parents=True)
    (stale / "bin" / "python").write_text("stale\n")
    inst = Instance(name="V", version="17.0", path=str(base),
                    venv_path=str(stale),
                    community_path=str(base / "community"),
                    status=status)
    assert create_instance(inst).ok
    return inst, base


def _stub_success(monkeypatch, tmp_path):
    def _create(path, progress_cb=None, cancel=None):
        return Result.success(data={"venv_path": str(tmp_path / "newvenv")},
                              message="created")
    def _install(venv, community, progress_cb=None, cancel=None):
        return Result.success(data={}, message="installed")
    def _stream(cmd, progress_cb=None, cancel=None, **kw):
        return Result.success(data={}, message="pkg_resources ok")
    monkeypatch.setattr(venv_manager, "create_venv", _create)
    monkeypatch.setattr(venv_manager, "install_requirements", _install)
    monkeypatch.setattr(venv_manager, "run_streaming", _stream)


def test_rebuild_unknown_and_running(_env):
    assert not venv_manager.rebuild_venv("no-such-id").ok
    inst, _ = _make_instance(_env, status="running")
    res = venv_manager.rebuild_venv(inst.id)
    assert not res.ok and "Stop" in res.message


def test_rebuild_replaces_stale_venv(_env, monkeypatch):
    tmp_path = _env
    inst, base = _make_instance(tmp_path)
    _stub_success(monkeypatch, tmp_path)
    lines = []
    res = venv_manager.rebuild_venv(inst.id, progress_cb=lines.append)
    assert res.ok, res.message
    assert not (base / "venv").exists(), "stale venv must be removed"
    assert any("Removing stale venv" in line for line in lines)
    updated = get_instance(inst.id)
    assert updated.venv_path == str(tmp_path / "newvenv")


def test_rebuild_install_failure_propagates(_env, monkeypatch):
    tmp_path = _env
    inst, _ = _make_instance(tmp_path)
    monkeypatch.setattr(
        venv_manager, "create_venv",
        lambda path, progress_cb=None, cancel=None: Result.success(
            data={"venv_path": str(tmp_path / "newvenv")}, message="created"))
    monkeypatch.setattr(
        venv_manager, "install_requirements",
        lambda *a, **k: Result.failure("pip exploded"))
    res = venv_manager.rebuild_venv(inst.id)
    assert not res.ok and "pip exploded" in res.message
