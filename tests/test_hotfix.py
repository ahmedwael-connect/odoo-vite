"""Hotfix sprint regression tests: H-B1 packaging, H-B2 adopted venv,
H-B3 missing-DB removal. Live Postgres probes skip when unreachable."""

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


# ------------------------------------------------------------------ H-B1
def test_create_venv_installs_setuptools_first(tmp_path, monkeypatch):
    from odoo_vite.core import venv_manager

    calls = []

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0, cwd=None, env=None):
        calls.append(cmd)
        return Result.success(data={"lines": [], "returncode": 0,
                                    "cancelled": False})

    monkeypatch.setattr(venv_manager, "run_streaming", _fake)
    base = tmp_path / "i"
    base.mkdir()
    res = venv_manager.create_venv(base)
    assert res.ok, res.message
    # create_venv only builds the venv; packaging lands in install_requirements
    # (immediately after, in the provisioning pipeline).
    assert len(calls) == 1 and calls[0][:3] == ["python3", "-m", "venv"]


def test_install_requirements_upgrades_packaging(tmp_path, monkeypatch):
    from odoo_vite.core import venv_manager

    calls = []

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0, cwd=None, env=None):
        calls.append(cmd)
        return Result.success(data={"lines": [], "returncode": 0,
                                    "cancelled": False})

    monkeypatch.setattr(venv_manager, "run_streaming", _fake)
    venv = tmp_path / "v"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "pip").touch()
    (venv / "bin" / "python").touch()
    comm = tmp_path / "c"
    comm.mkdir()
    (comm / "requirements.txt").write_text("six\n")
    res = venv_manager.install_requirements(venv, comm)
    assert res.ok, res.message
    joined = "\n".join(" ".join(c) for c in calls)
    assert "setuptools" in joined and "-r" in joined


def test_repair_venv_runs_packaging_and_verify(tmp_path, db, monkeypatch):
    from odoo_vite.core import venv_manager

    calls = []

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0, cwd=None, env=None):
        calls.append(cmd)
        return Result.success(data={"lines": ["pkg_resources ok"],
                                    "returncode": 0, "cancelled": False})

    monkeypatch.setattr(venv_manager, "run_streaming", _fake)
    base = tmp_path / "i"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "pip").touch()
    (base / "venv" / "bin" / "python").touch()
    inst = Instance(name="R", path=str(base),
                    venv_path=str(base / "venv"), status="stopped")
    assert create_instance(inst, db).ok
    res = venv_manager.repair_venv(inst.id, db_path=db)
    assert res.ok, res.message
    joined = "\n".join(" ".join(c) for c in calls)
    assert "setuptools" in joined
    assert "pkg_resources" in joined  # verification step ran


def test_repair_venv_no_venv_recorded(db):
    from odoo_vite.core import venv_manager

    inst = Instance(name="NoVenv", path="/tmp/x", venv_path="",
                    mode="adopted", status="stopped")
    from odoo_vite.core.registry import create_instance as _create

    assert _create(inst, db).ok
    res = venv_manager.repair_venv(inst.id, db_path=db)
    assert not res.ok and "No Python environment recorded" in res.message


# ------------------------------------------------------------------ H-B2
def test_adopted_start_without_venv_names_editor(tmp_path, db):
    from odoo_vite.core import process_manager

    inst = Instance(name="Ad", mode="adopted", path="/srv/a",
                    community_path="/srv/a/community",
                    conf_path="/srv/a/odoo.conf", venv_path="",
                    primary_db="ad_db", status="stopped", db_created=True)
    assert create_instance(inst, db).ok
    res = process_manager.start_instance(inst.id, db_path=db)
    assert not res.ok
    assert "detail page" in res.message
    assert "re-run provisioning" not in res.message
    assert "missing at bin/python" not in res.message  # no relative-path red herring


def test_adopted_start_uses_recorded_venv(tmp_path, db, monkeypatch):
    import subprocess

    from odoo_vite.core import process_manager

    launched = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            launched.append(list(cmd))
            self.pid = 999001
            self.returncode = 0

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    base = tmp_path / "custom"
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir()
    (base / "community" / "odoo-bin").touch()
    (base / "odoo.conf").touch()
    inst = Instance(name="Ad2", mode="adopted", path=str(base),
                    venv_path=str(base / "venv"),
                    community_path=str(base / "community"),
                    conf_path=str(base / "odoo.conf"),
                    log_path=str(base / "odoo.log"),
                    db_user="odoo", db_password="x", primary_db="ad_db",
                    status="stopped", db_created=True)
    assert create_instance(inst, db).ok
    from odoo_vite.core.db_state import DbState

    monkeypatch.setattr("odoo_vite.core.db_state.get_db_state",
                        lambda *a, **k: DbState(db_name="x", exists=True,
                                              initialized=True))
    res = process_manager.start_instance(inst.id, db_path=db)
    assert res.ok, res.message
    assert launched[0][0] == str(base / "venv" / "bin" / "python")


def test_adopt_validates_venv_override(tmp_path, db):
    from odoo_vite.core import adopt

    root = tmp_path / "e"
    (root / "community").mkdir(parents=True)
    (root / "community" / "odoo-bin").touch()
    (root / "odoo.conf").write_text("[options]\n")
    res = adopt.adopt_instance("V", root / "odoo.conf", root / "community",
                               overrides={"primary_db": "v_db",
                                          "venv_path": str(root / "novenv")},
                               db_path=db)
    assert not res.ok and "bin/python" in res.message


# ------------------------------------------------------------------ H-B3
def test_remove_skips_missing_db_and_completes(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager, removal

    monkeypatch.setattr(db_manager, "server_reachable", lambda: True)
    monkeypatch.setattr(db_manager, "database_exists", lambda *a, **k: False)

    def _boom(*a, **k):
        raise AssertionError("drop must not run for a missing DB")

    monkeypatch.setattr(db_manager, "drop_database", _boom)
    base = tmp_path / "gone"
    base.mkdir()
    inst = Instance(name="Gone", mode="managed", path=str(base),
                    primary_db="never_created_db", tracked_dbs=["never_created_db"],
                    status="stopped")
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert res.ok, res.message
    assert "nothing to drop" in res.message
    assert res.data["db_dropped"] is False
    assert not base.exists()
    assert get_instance(inst.id, db) is None


def test_remove_aborts_drops_when_server_down(tmp_path, db, monkeypatch):
    from odoo_vite.core import db_manager, removal

    monkeypatch.setattr(db_manager, "server_reachable", lambda: False)
    base = tmp_path / "down"
    base.mkdir()
    inst = Instance(name="Down", mode="managed", path=str(base),
                    primary_db="maybe_db", tracked_dbs=["maybe_db"],
                    status="stopped")
    assert create_instance(inst, db).ok
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert not res.ok and "unreachable" in res.message
    assert base.exists() and get_instance(inst.id, db) is not None


# ------------------------------------------------------------------ H-P1
def test_discover_filter_logic():
    """H-P1 client-side filter (GTK-free fakes — pytest-safe headless)."""
    from odoo_vite.ui.window_main import filter_checks

    class FakeCheck:
        def __init__(self):
            self.visible = True

        def set_visible(self, v):
            self.visible = v

    checks = {f"db_{i:02d}": FakeCheck() for i in range(17)}
    assert filter_checks(checks, "") == 17
    assert all(c.visible for c in checks.values())
    assert filter_checks(checks, "db_0") == 10
    assert sum(1 for c in checks.values() if c.visible) == 10
    assert filter_checks(checks, "DB_01") == 1  # case-insensitive
    assert filter_checks(checks, "zzz") == 0
