"""Dev Mode Watch ops: sessions, debounce -> exactly one restart, gates.

The pure debounce/scope half lives in core/devwatch (covered by
test_sprint11); this file proves the ops wiring the spec asks for:
a simulated burst of change events triggers EXACTLY one restart,
running-state gating skips restarts for stopped instances, and the
session lifecycle (start/double-start/stop/stop-again) behaves.
"""

from __future__ import annotations

import shutil
import time

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance
from odoo_vite.core.result import Result
from odoo_vite.ops import devwatch as watch_mod
from odoo_vite.ops.devwatch import DevWatchOps


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "reg.db"))
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("ODOO_VITE_LOCKS", str(tmp_path / "locks"))
    return tmp_path


def _register(tmp_path) -> tuple[Instance, object]:
    base = tmp_path / "i"
    (base / "custom_addons").mkdir(parents=True, exist_ok=True)
    (base / "community").mkdir(parents=True, exist_ok=True)
    inst = Instance(
        name="W", version="17.0", path=str(base),
        venv_path=str(base / "venv"),
        custom_addons_path=str(base / "custom_addons"),
        community_path=str(base / "community"),
        conf_path=str(base / "odoo.conf"), log_path=str(base / "odoo.log"),
        port=8098, db_user="odoo", db_password="odoo",
        primary_db="w_db", status="stopped", db_created=True,
    )
    res = create_instance(inst)
    assert res.ok, res.message
    return inst, base


def _wait(predicate, timeout: float = 6.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _counting_restart(sink: list):
    async def restart(instance_id: str) -> Result:
        sink.append(instance_id)
        return Result.success(message="restarted")

    return restart


# ------------------------------------------------------------------ lifecycle


def test_start_status_stop(db):
    inst, base = _register(db)
    events: list[dict] = []
    ops = DevWatchOps(on_event=events.append,
                      restart_cb=_counting_restart([]),
                      quiet_seconds=0.15)
    started = ops.start(inst.id)
    assert started.ok, started.message
    status = ops.status(inst.id)
    assert status["watching"] is True
    assert str(base / "custom_addons") in status["roots"]
    assert str(base / "community") in status["roots"]

    again = ops.start(inst.id)
    assert again.ok is False and "already" in again.message

    stopped = ops.stop(inst.id)
    assert stopped.ok, stopped.message
    assert ops.status(inst.id)["watching"] is False
    assert ops.stop(inst.id).ok is False

    states = [e["state"] for e in events]
    assert states == ["started", "stopped"]
    assert events[0]["instance_id"] == inst.id


def test_start_fails_without_roots(db):
    inst, base = _register(db)
    shutil.rmtree(base / "custom_addons")
    shutil.rmtree(base / "community")
    ops = DevWatchOps(restart_cb=_counting_restart([]))
    res = ops.start(inst.id)
    assert res.ok is False
    assert "Nothing to watch" in res.message


# ----------------------------------------------------- debounce -> one restart


def test_burst_of_changes_fires_exactly_one_restart(db, monkeypatch):
    monkeypatch.setattr(watch_mod, "_running", lambda _iid: True)
    inst, base = _register(db)
    restarts: list[str] = []
    ops = DevWatchOps(restart_cb=_counting_restart(restarts),
                      quiet_seconds=0.2)
    assert ops.start(inst.id).ok

    target = base / "custom_addons" / "mod_a"
    target.mkdir()
    for i in range(5):  # burst: five writes well inside the quiet window
        (target / f"f{i}.py").write_text("# x\n", encoding="utf-8")
        time.sleep(0.02)

    assert _wait(lambda: len(restarts) >= 1), "first burst never restarted"
    time.sleep(0.7)  # settle window: no second fire from the same burst
    assert len(restarts) == 1, f"burst caused {len(restarts)} restarts"

    for i in range(3):  # a second, separate burst -> a second restart
        (target / f"g{i}.py").write_text("# y\n", encoding="utf-8")
    assert _wait(lambda: len(restarts) >= 2), "second burst never restarted"

    status = ops.status(inst.id)
    assert status["fires"] == 2
    assert status["changes"] == 2  # one arm per burst
    assert ops.stop(inst.id).ok


def test_quiet_change_skips_restart_when_not_running(db, monkeypatch):
    monkeypatch.setattr(watch_mod, "_running", lambda _iid: False)
    inst, base = _register(db)
    restarts: list[str] = []
    events: list[dict] = []
    ops = DevWatchOps(on_event=events.append,
                      restart_cb=_counting_restart(restarts),
                      quiet_seconds=0.15)
    assert ops.start(inst.id).ok

    # Drive the debounce directly (no observer timing involved).
    session = ops._sessions[inst.id]
    session.feed(str(base / "custom_addons" / "probe.py"))
    assert _wait(lambda: any(e["state"] == "skipped" for e in events))
    assert restarts == []
    assert ops.stop(inst.id).ok


def test_non_watchable_files_never_arm(db, monkeypatch):
    monkeypatch.setattr(watch_mod, "_running", lambda _iid: True)
    inst, base = _register(db)
    restarts: list[str] = []
    ops = DevWatchOps(restart_cb=_counting_restart(restarts),
                      quiet_seconds=0.1)
    assert ops.start(inst.id).ok
    session = ops._sessions[inst.id]
    session.feed(str(base / "custom_addons" / "__pycache__" / "x.py"))
    session.feed(str(base / "custom_addons" / "readme.md"))
    session.feed(str(base / "custom_addons" / ".hidden.py"))
    time.sleep(0.4)
    assert session.controller.pending is False
    assert restarts == []
    assert ops.stop(inst.id).ok


def test_read_events_never_arm_the_debounce():
    """Odoo's startup IMPORTS every .py in the watched roots (open +
    close_no_write). Those must never feed the debounce — feeding them
    caused an endless restart loop in the first e2e run."""
    from watchdog.events import (
        FileClosedEvent,
        FileClosedNoWriteEvent,
        FileCreatedEvent,
        FileModifiedEvent,
        FileOpenedEvent,
    )

    fed: list[str] = []
    handler = watch_mod._Handler(fed.append)
    for evt in (
        FileOpenedEvent("/roots/custom/probe.py"),
        FileClosedNoWriteEvent("/roots/custom/probe.py"),
        FileClosedEvent("/roots/custom/probe.py"),  # close of a write: modify feeds already
    ):
        handler.on_any_event(evt)
    assert fed == [], f"read events armed the debounce: {fed}"

    handler.on_any_event(FileModifiedEvent("/roots/custom/probe.py"))
    handler.on_any_event(FileCreatedEvent("/roots/custom/new.py"))
    assert len(fed) == 2
