"""Web facade (Phase 1): envelopes, sink mapping, progress/cancel, safety.

Headless by design — no pywebview, no window: the Api object is plain
Python, which is the whole point of putting the facade below the window.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance
from odoo_vite.core.result import Result
from odoo_vite.ui_web import Api, ListTransport, PushChannel, create_api
from odoo_vite.ui_web.serialize import to_payload


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "reg.db"))
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setenv("ODOO_VITE_LOCKS", str(tmp_path / "locks"))
    events = ListTransport()
    api = Api(PushChannel(events))
    return api, events


def _register(inst: Instance) -> str:
    res = create_instance(inst)
    assert res.ok, res.message
    return inst.id


# ------------------------------------------------------------------ serialize


def test_result_payload_is_json_safe():
    payload = to_payload(
        Result(ok=True, message="m", data={"p": Path("/tmp/x"), "t": (1, Path("y"))})
    )
    assert json.loads(json.dumps(payload))["data"]["p"] == "/tmp/x"
    assert payload["data"]["t"] == [1, "y"]


def test_instance_to_payload_is_a_dict():
    payload = to_payload(Instance(name="z"))
    assert payload["name"] == "z"
    assert isinstance(payload["id"], str) and payload["id"]


def test_transport_failure_never_breaks_emit():
    def boom(_env):
        raise RuntimeError("window gone")

    PushChannel(boom).emit("message", {"text": "hi"})  # must not raise


# ------------------------------------------------------------------ lifecycle


def test_lifecycle_start_emits_message_and_refresh(env, monkeypatch):
    api, events = env

    def fake_start(instance_id, database=None, confirm_cb=None):
        return Result(ok=True, message="Instance started", data={"pid": 42})

    monkeypatch.setattr("odoo_vite.core.process_manager.start_instance", fake_start)

    res = api.lifecycle.start("i1")

    assert res == {"ok": True, "message": "Instance started", "data": {"pid": 42}}
    msg = events.last("message")
    assert msg["payload"] == {"text": "Instance started", "level": "info"}
    assert events.last("refresh") is not None
    # everything is JSON-safe
    json.dumps(events.events)


def test_lifecycle_start_confirm_contract(env, monkeypatch):
    api, events = env
    captured = []

    def fake_start(instance_id, database=None, confirm_cb=None):
        captured.append(confirm_cb)
        if confirm_cb is None:
            return Result(
                ok=False,
                message="confirmation required",
                data={"needs_confirm": True, "preview": {"database": "demo"}},
            )
        return Result(
            ok=True,
            message="started",
            data={"confirmed": bool(confirm_cb({"database": "demo"}))},
        )

    monkeypatch.setattr("odoo_vite.core.process_manager.start_instance", fake_start)

    first = api.lifecycle.start("i1")
    second = api.lifecycle.start("i1", confirm=True)

    assert first["ok"] is False
    assert first["data"]["needs_confirm"] is True
    assert captured[0] is None  # first call: core shows its own preview
    assert second["ok"] is True
    assert second["data"]["confirmed"] is True
    assert captured[1] is not None  # second call: user approved
    assert events.last("progress-done") is None  # lifecycle has no progress


def test_unexpected_exception_becomes_ok_false(env, monkeypatch):
    api, _events = env

    def boom(*_a, **_k):
        raise RuntimeError("kaput")

    monkeypatch.setattr("odoo_vite.core.process_manager.stop_instance", boom)
    res = api.lifecycle.stop("i1")
    assert res["ok"] is False
    assert "RuntimeError" in res["message"]
    assert "kaput" in res["message"]


# ------------------------------------------------------------------ databases


def test_db_states_sink_maps_to_event(env, monkeypatch):
    api, events = env
    iid = _register(Instance(name="st", tracked_dbs=["pgdb"], primary_db="pgdb"))
    monkeypatch.setattr(
        "odoo_vite.ops.databases.get_db_state",
        lambda db, user, pw: SimpleNamespace(
            exists=True,
            initialized=True,
            odoo_version="18.0",
            size_bytes=2048,
            owner="odoo",
        ),
    )

    states = api.databases.refresh_states(iid)

    assert states["pgdb"]["exists"] is True
    envd = events.last("db-states")
    assert envd["payload"]["instance_id"] == iid
    assert envd["payload"]["states"]["pgdb"]["odoo_version"] == "18.0"
    json.dumps(events.events)


# -------------------------------------------------------------------- modules


def _fake_install_factory(progress_lines):
    def fake(inst, db_name, mods, progress_cb=None, cancel=None, db_path=None):
        for line in progress_lines:
            progress_cb(line)
        return Result(ok=True, message="Installed: " + ", ".join(mods), data={})

    return fake


def test_install_streams_progress_and_always_finishes(env, monkeypatch):
    api, events = env
    iid = _register(Instance(name="mod", primary_db="odoo", tracked_dbs=["odoo"]))
    monkeypatch.setattr(
        "odoo_vite.core.module_manager.install_modules",
        _fake_install_factory(["resolving", "loading"]),
    )

    res = api.modules.install(iid, ["base"], op_id="op-1")

    assert res["ok"] is True
    lines = [e for e in events.events if e["kind"] == "progress-line"]
    assert [e["payload"]["line"] for e in lines] == ["resolving", "loading"]
    assert all(e["payload"]["op_id"] == "op-1" for e in lines)
    done = events.last("progress-done")
    assert done["payload"] == {"op_id": "op-1"}
    assert events.events[-1]["kind"] == "progress-done"  # done is last, always


def test_install_failure_still_emits_progress_done(env, monkeypatch):
    api, events = env
    iid = _register(Instance(name="mod2", primary_db="odoo", tracked_dbs=["odoo"]))

    def fake(*_a, **_k):
        return Result(ok=False, message="odoo exploded", data=None)

    monkeypatch.setattr("odoo_vite.core.module_manager.install_modules", fake)
    res = api.modules.install(iid, ["base"], op_id="op-2")
    assert res["ok"] is False
    assert events.last("progress-done")["payload"] == {"op_id": "op-2"}


def test_cancel_sets_event_and_registry_cleans_up(env, monkeypatch):
    api, events = env
    iid = _register(Instance(name="mod3", primary_db="odoo", tracked_dbs=["odoo"]))
    started = threading.Event()

    def fake(*_a, **_k):
        cancel = _k.get("cancel")
        started.set()
        for _ in range(150):
            if cancel is not None and cancel():
                return Result(ok=False, message="cancelled by user")
            time.sleep(0.02)
        return Result(ok=True, message="finished")

    monkeypatch.setattr("odoo_vite.core.module_manager.install_modules", fake)

    out = {}

    def run():
        out["res"] = api.modules.install(iid, ["base"], op_id="op-c")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert started.wait(5), "op never started"
    assert api.modules.cancel("op-c")["ok"] is True

    worker.join(10)
    assert not worker.is_alive()
    assert out["res"]["ok"] is False
    assert "cancelled" in out["res"]["message"]
    assert api.modules.cancel("op-c")["ok"] is False  # registry cleaned up
    assert events.last("progress-done")["payload"] == {"op_id": "op-c"}


# ----------------------------------------------------------------------- app


def test_app_instances_and_statuses_round_trip(env):
    api, _events = env
    iid = _register(Instance(name="Web Demo", port=8070))

    instances = api.app.instances()
    assert [i["name"] for i in instances] == ["Web Demo"]
    json.dumps(instances)

    statuses = api.app.statuses()
    assert {s["id"] for s in statuses} >= {iid}
    json.dumps(statuses)


def test_app_pick_file_wiring():
    unwired, _push = create_api(None)
    assert unwired.app.pick_file()["ok"] is False

    def dialog(mode, title, pattern):
        return f"/tmp/{mode}-result.tar.gz"

    wired, _push = create_api(None, file_dialog=dialog)
    assert wired.app.pick_file(title="Bundle") == {
        "ok": True,
        "path": "/tmp/open-result.tar.gz",
    }
    assert wired.app.pick_dir()["path"] == "/tmp/folder-result.tar.gz"


# ---------------------------------------------------------------------- logs


def test_logs_tail_reads_last_lines(env, tmp_path):
    api, _events = env
    log = tmp_path / "odoo.log"
    log.write_text("one\ntwo\nthree\n")
    iid = _register(Instance(name="tailer", log_path=str(log)))

    res = api.logs.tail(iid, n=2)
    assert res["ok"] is True
    assert res["lines"] == ["two", "three"]

    missing = api.logs.tail("nope")
    assert missing["ok"] is False
    assert missing["lines"] == []


def test_app_instance_and_enterprise(env):
    api, _events = env
    iid = _register(Instance(name="ent", version="17.0"))
    row = api.app.instance(iid)
    assert row["name"] == "ent"
    assert api.app.instance("nope") is None

    ent = api.app.enterprise(iid)
    assert ent["ok"] is True
    assert ent["data"]["state"] == "community"
    assert api.app.enterprise("nope")["ok"] is False


def test_db_server_probe_and_backup_files(env):
    api, _events = env
    iid = _register(Instance(name="probe"))
    assert isinstance(api.databases.server_reachable(), bool)
    assert api.databases.list_backup_files(iid) == []
    assert api.databases.list_backup_files("nope") == []


def test_config_addons_state_and_heuristic(env, tmp_path):
    api, _events = env
    iid = _register(Instance(name="addons"))
    assert api.config.addons_state(iid) == []  # not migrated, no conf
    assert api.config.addons_state("nope") == []

    good = tmp_path / "addons"
    (good / "base").mkdir(parents=True)
    (good / "base" / "__manifest__.py").write_text("{}")
    assert api.config.looks_like_addons(str(good)) is True
    assert api.config.looks_like_addons(str(tmp_path / "empty")) is False


def test_devtools_shell_lifecycle(env, monkeypatch):
    api, _events = env
    iid = _register(Instance(name="shellie"))

    # headless: no real PTY spawn — stub the shell with a fake
    class FakeShell:
        running = False
        exit_code = None
        started = None

        def start(self, inst, db_name="", db_path=None):
            from odoo_vite.core.result import Result

            FakeShell.started = (inst.name, db_name)
            FakeShell.running = True
            return Result(ok=True, message="shell started")

        def stop(self):
            from odoo_vite.core.result import Result

            FakeShell.running = False
            return Result(ok=True, message="stopped")

        def send_line(self, line):
            from odoo_vite.core.result import Result

            return Result(ok=True, message=f"sent {line}")

        def drain_output(self, max_lines=500):
            return ["Odoo 17.0", ">>> "]

    api.devtools._shell = FakeShell()

    poll = api.devtools.shell_poll()
    assert poll == {"running": False, "exit_code": None, "lines": ["Odoo 17.0", ">>> "]}

    started = api.devtools.shell_start(iid)
    assert started["ok"] is True
    assert FakeShell.started == ("shellie", "")
    assert api.devtools.shell_poll()["running"] is True

    assert "sent print(1)" in api.devtools.shell_send("print(1)")["message"]
    assert api.devtools.shell_stop()["ok"] is True
    assert api.devtools.shell_poll()["running"] is False
    assert api.devtools.shell_start("nope")["ok"] is False


# -------------------------------------------------------------------- wizards


def test_wizard_pure_helpers_are_json_safe(env):
    api, _events = env
    assert isinstance(api.wizards.validate_details({}), str)  # empty -> error
    assert len(api.wizards.generate_password(12)) == 12
    assert isinstance(api.wizards.suggest_db_name("My App!"), str)
    assert api.wizards.cancel("never-registered")["ok"] is False


def test_build_adopt_overrides_passes_db_name_through(env):
    """Regression: the facade once passed a dict into core's db_name slot."""
    api, _events = env
    overrides = api.wizards.build_adopt_overrides(
        {}, {"db_password": "pw", "port": ""}, "mydb"
    )
    assert overrides["primary_db"] == "mydb"
    assert overrides["db_password"] == "pw"
    assert "port" not in overrides  # blanks dropped, never invented


def test_state_categories_batches_one_round_trip(env):
    api, _events = env
    cats = api.modules.state_categories(
        [
            {"name": "base", "state": "installed"},
            {"name": "web", "state": "uninstallable"},
            {"name": "sale", "state": "to upgrade"},
        ]
    )
    assert cats == {
        "base": "Installed",
        "web": "Installable",
        "sale": "Upgradeable",
    }
