"""Sprint 5 Part A tests: db_state shapes + the cross-caller agreement guard.

The agreement test is the structural regression guard for the whole bug
class (RC BUG-3, H-B3, reported create-prompt-on-live-DB): one canned state,
three callers, all must read it the same way.
"""

import pytest

from odoo_vite.core.db_state import DbState, get_db_state, human_size, odoo_major
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


def _canned(monkeypatch, **state):
    import odoo_vite.core.db_state as mod

    defaults = {"db_name": "x", "exists": True, "initialized": True,
                "odoo_version": "17.0.1.3", "size_bytes": 123,
                "owner": "odoo", "error": None}
    defaults.update(state)
    canned = DbState(**defaults)
    monkeypatch.setattr(mod, "get_db_state", lambda *a, **k: canned)
    return canned


def _inst(base, **overrides):
    kwargs = {"name": "Agree", "version": "17.0", "path": str(base),
              "venv_path": str(base / "venv"),
              "community_path": str(base / "community"),
              "conf_path": str(base / "odoo.conf"),
              "log_path": str(base / "logs" / "odoo.log"),
              "port": 8092, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "agree_db", "tracked_dbs": ["agree_db"],
              "status": "stopped", "db_created": False}
    kwargs.update(overrides)
    return Instance(**kwargs)


def _fs(base):
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").touch()


# ------------------------------------------------------- state shapes
def test_state_initialized(monkeypatch):
    import odoo_vite.core.db_state as mod

    calls = []

    def _fake(cmd, env_extra=None, timeout=30):
        calls.append(cmd)
        if "pg_database WHERE" in cmd[-1]:
            return 0, "agree_db|odoo|20971520"
        if "ir_module_module" in cmd[-1]:
            return 0, "17.0.1.3"
        raise AssertionError(cmd)

    monkeypatch.setattr(mod, "_run", _fake)
    st = get_db_state("agree_db", "odoo", "pw")
    assert st == DbState(db_name="agree_db", exists=True, initialized=True,
                         odoo_version="17.0.1.3", size_bytes=20971520,
                         owner="odoo", error=None)
    assert len(calls) == 2, "one catalog query + one init probe"


def test_state_empty_db(monkeypatch):
    import odoo_vite.core.db_state as mod

    def _fake(cmd, env_extra=None, timeout=30):
        if "pg_database WHERE" in cmd[-1]:
            return 0, "empty_db|odoo|8192"
        return 1, 'relation "ir_module_module" does not exist'

    monkeypatch.setattr(mod, "_run", _fake)
    st = get_db_state("empty_db", "odoo", "pw")
    assert st.exists is True and st.initialized is False
    assert st.odoo_version is None and st.owner == "odoo"


def test_state_missing_db(monkeypatch):
    import odoo_vite.core.db_state as mod

    monkeypatch.setattr(mod, "_run", lambda *a, **k: (0, ""))
    st = get_db_state("nope", "odoo", "pw")
    assert st.exists is False and st.initialized is False


def test_state_server_down(monkeypatch):
    import odoo_vite.core.db_state as mod

    monkeypatch.setattr(mod, "_run", lambda *a, **k: (-1, "connection refused"))
    st = get_db_state("whatever", "odoo", "pw")
    assert st.exists is False and st.error is not None


def test_helpers():
    assert odoo_major("17.0.1.3") == "17.0"
    assert odoo_major(None) == "" and odoo_major("junk") == ""
    assert human_size(20971520) == "20.0 MB"
    assert human_size(None) == "—"


def test_wrappers_delegate(monkeypatch):
    from odoo_vite.core import db_manager

    _canned(monkeypatch, exists=True, initialized=True)
    assert db_manager.database_exists("x") is True
    assert db_manager.database_initialized("x") is True
    _canned(monkeypatch, exists=False, initialized=False)
    assert db_manager.database_exists("x") is False


# --------------------------------------- agreement: one state, three callers
def test_agreement_on_live_db(tmp_path, db, monkeypatch):
    """Canned initialized DB: Start collides (never creates), Switch goes
    plain, Remove drops. All three read the same state object semantics."""
    import subprocess

    from odoo_vite.core import db_manager, process_manager, removal

    _canned(monkeypatch)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)

    launched = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            launched.append(list(cmd))
            self.pid = 111
            self.returncode = 0

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok

    # 1. Start on a live DB some other flow owns → collision, no confirm, no launch
    seen = []
    res = process_manager.start_instance(
        inst.id, confirm_cb=lambda pv: seen.append(pv) or True, db_path=db)
    assert not res.ok and res.data.get("collision") is True
    assert seen == [], "confirm must never fire for an existing DB (reported bug)"
    assert launched == []

    # 2. Switch to the same live DB while "running" → plain -d start, no -i.
    # (Simulating the Reuse choice first: db_created flips True, as the UI does.)
    from odoo_vite.core.registry import update_instance

    update_instance(inst.id, db, db_created=True, status="running", pid=999999)
    alive = {"on": True}
    monkeypatch.setattr(process_manager, "_alive_pid",
                        lambda inst: 999999 if alive["on"] else None)

    def _stop(*a, **k):
        alive["on"] = False
        update_instance(inst.id, db, status="stopped", pid=None)
        return Result.success()

    monkeypatch.setattr(process_manager, "stop_instance", _stop)
    res = process_manager.switch_database(inst.id, "agree_db", db_path=db)
    assert res.ok, res.message
    assert "-i" not in launched[0]

    # 3. Remove with drop → drop attempted (exists!), then files+row gone
    dropped = []
    monkeypatch.setattr(db_manager, "server_reachable", lambda: True)
    monkeypatch.setattr(db_manager, "drop_database",
                        lambda name, *a, **k: dropped.append(name) or
                        Result.success())
    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert res.ok, res.message
    assert dropped == ["agree_db"]
    assert get_instance(inst.id, db) is None


def test_agreement_on_missing_db(tmp_path, db, monkeypatch):
    """Canned missing DB: Start needs confirm (no collision), Switch routes
    to creation, Remove skips the drop and still completes."""
    from odoo_vite.core import process_manager, removal

    _canned(monkeypatch, exists=False, initialized=False,
            odoo_version=None, size_bytes=None, owner=None)
    monkeypatch.setattr(process_manager, "_alive_pid", lambda inst: None)
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok

    res = process_manager.start_instance(inst.id, confirm_cb=None, db_path=db)
    assert not res.ok and res.data.get("needs_confirm") is True
    assert "collision" not in (res.data or {})

    res = removal.remove_instance(inst.id, drop_db=True, db_path=db)
    assert res.ok and "nothing to drop" in res.message
    assert get_instance(inst.id, db) is None
