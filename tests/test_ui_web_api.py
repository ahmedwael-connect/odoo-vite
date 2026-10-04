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
from odoo_vite.core.registry import create_instance, update_instance
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


# ------------------------------------------------------------------------- theme


def test_theme_api_roundtrip(env):
    api, _events = env
    assert api.app.theme() == "system"
    res = api.app.save_theme("light")
    assert res["ok"] is True, res.get("message")
    assert api.app.theme() == "light"
    bad = api.app.save_theme("neon")
    assert bad["ok"] is False
    assert api.app.theme() == "light"  # unchanged on invalid


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


def test_app_instances_never_expose_db_password(env):
    """3.1.0 B2: secrets must never cross into the JS context."""
    api, _events = env
    iid = _register(Instance(name="Secret", port=8071,
                             db_password="s3cret-pw"))

    rows = api.app.instances()
    assert "db_password" not in rows[0]
    assert "s3cret-pw" not in json.dumps(rows)

    single = api.app.instance(iid)
    assert single is not None
    assert "db_password" not in single
    assert "s3cret-pw" not in json.dumps(single)


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


def test_app_path_exists(tmp_path):
    """3.2.0 P0: the UI's overwrite guard probe."""
    api, _push = create_api(None)
    target = tmp_path / "bundle.tar.gz"
    assert api.app.path_exists(str(target)) is False
    target.write_text("x")
    assert api.app.path_exists(str(target)) is True
    assert api.app.path_exists("") is False
    assert api.app.path_exists(str(tmp_path / "nope")) is False


# ---------------------------------------------------------------------- logs


def test_logs_tail_reads_last_lines(env, tmp_path):
    api, _events = env
    log = tmp_path / "odoo.log"
    log.write_text("one\ntwo\nthree\n")
    iid = _register(Instance(name="tailer", log_path=str(log)))

    res = api.logs.tail(iid, n=2)
    assert res["ok"] is True
    assert res["lines"] == ["two", "three"]
    assert res["initial"] is True
    assert res["rotated"] is False

    missing = api.logs.tail("nope")
    assert missing["ok"] is False
    assert missing["lines"] == []


def test_logs_tail_incremental_rotation_and_reanchor(env, tmp_path):
    api, _events = env
    log = tmp_path / "odoo.log"
    log.write_text("one\ntwo\n")
    iid = _register(Instance(name="tailer2", log_path=str(log)))

    first = api.logs.tail(iid)
    assert first["ok"] is True
    assert first["initial"] is True
    assert first["lines"] == ["one", "two"]

    # idle poll: no new bytes -> empty append (frontend leaves view alone)
    assert api.logs.tail(iid) == {
        "ok": True, "lines": [], "initial": False, "rotated": False,
    }

    with log.open("a") as fh:
        fh.write("three\n")
    append = api.logs.tail(iid)
    assert append == {"ok": True, "lines": ["three"], "initial": False,
                      "rotated": False}
    # already-seen bytes are never re-emitted
    assert api.logs.tail(iid)["lines"] == []

    # (a) in-place truncation: file smaller than the known offset
    log.write_text("tiny\n")
    trunc = api.logs.tail(iid)
    assert trunc["rotated"] is True
    assert trunc["lines"] == ["tiny"]
    with log.open("a") as fh:
        fh.write("after-tiny\n")
    assert api.logs.tail(iid)["lines"] == ["after-tiny"]

    # (b) inode rotation: fresh file already larger than the old offset —
    #     size checks alone would seek into the middle and emit garbage.
    log.rename(tmp_path / "odoo.log.1")
    log.write_text("".join(f"line-{i}\n" for i in range(50)))
    rot = api.logs.tail(iid)
    assert rot["initial"] is False
    assert rot["rotated"] is True
    assert len(rot["lines"]) == 50
    assert rot["lines"][-1] == "line-49"
    with log.open("a") as fh:
        fh.write("post-rotation\n")
    assert api.logs.tail(iid)["lines"] == ["post-rotation"]

    # log_path change on the same instance re-anchors (fresh initial fill)
    other = tmp_path / "other.log"
    other.write_text("alpha\nbeta\n")
    assert update_instance(iid, log_path=str(other)).ok
    re = api.logs.tail(iid)
    assert re["initial"] is True
    assert re["lines"] == ["alpha", "beta"]


# --------------------------------------------------------------------- audit


def test_audit_tail_returns_events(env):
    from odoo_vite.core import audit as audit_mod

    api, _events = env
    audit_mod.log_event("id-1", "Demo", "start", "pid=1")
    audit_mod.log_event("id-1", "Demo", "stop", "graceful")

    rows = api.audit.tail()
    assert [r["action"] for r in rows] == ["start", "stop"]
    assert rows[0]["detail"] == "pid=1"
    assert len(api.audit.tail(limit=1)) == 1
    # non-numeric limit falls back to the default instead of raising
    assert len(api.audit.tail(limit="bogus")) == 2


def test_audit_tail_missing_file_is_empty(env):
    api, _events = env
    assert api.audit.tail() == []


# -------------------------------------------------------------------- watch


def test_watch_lifecycle(env, tmp_path):
    api, _events = env
    assert api.watch.start("missing")["ok"] is False

    base = tmp_path / "winst"
    (base / "custom_addons").mkdir(parents=True)
    (base / "community").mkdir(parents=True)
    inst = Instance(
        name="watchy", version="17.0", path=str(base),
        venv_path=str(base / "venv"),
        custom_addons_path=str(base / "custom_addons"),
        community_path=str(base / "community"),
        conf_path=str(base / "odoo.conf"), log_path=str(base / "odoo.log"),
        port=8099, db_user="odoo", db_password="odoo",
        primary_db="w_db", status="stopped", db_created=True,
    )
    iid = _register(inst)
    started = api.watch.start(iid)
    assert started["ok"] is True, started
    try:
        status = api.watch.status(iid)
        assert status["watching"] is True
        assert str(base / "custom_addons") in status["roots"]
        assert api.watch.start(iid)["ok"] is False  # double-start refused
        stopped = api.watch.stop(iid)
        assert stopped["ok"] is True, stopped
        assert api.watch.status(iid)["watching"] is False
        assert api.watch.stop(iid)["ok"] is False
    finally:
        api.watch.stop(iid)  # idempotent cleanup — harmless if already stopped


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
    (good / "website").mkdir()
    (good / "website" / "__manifest__.py").write_text("{}")
    assert api.config.looks_like_addons(str(good)) is True
    assert api.config.looks_like_addons(str(tmp_path / "empty")) is False

    # enrichment: every entry carries exists + modules for the manager UI
    conf = tmp_path / "odoo.conf"
    conf.write_text(f"[options]\naddons_path = {good},{tmp_path / 'gone'}\n")
    iid2 = _register(Instance(name="addons2", conf_path=str(conf)))
    rows = api.config.addons_state(iid2)
    assert [r["path"] for r in rows] == [str(good), str(tmp_path / "gone")]
    assert rows[0] == {"path": str(good), "enabled": True, "type": "extra",
                       "exists": True, "modules": 2}
    assert rows[1] == {"path": str(tmp_path / "gone"), "enabled": True,
                       "type": "unknown",
                       "exists": False, "modules": 0}


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


def test_install_requirements_streams_progress(env, monkeypatch):
    from odoo_vite.core import system_check

    seen = {}

    def fake_install(missing, on_line=None, dry_run=False):
        seen["missing"] = list(missing)
        on_line("Get:1 git/stable amd64")
        on_line("Setting up git")
        return Result.success(message="installed git")

    monkeypatch.setattr(system_check, "install_requirements", fake_install)
    api, events = env
    res = api.wizards.install_requirements(["git"], "op-inst-1")
    assert res["ok"] is True
    assert res["message"] == "installed git"
    assert seen["missing"] == ["git"]
    lines = [e for e in events.events if e["kind"] == "progress-line"]
    assert [e["payload"]["line"] for e in lines] == [
        "Get:1 git/stable amd64", "Setting up git"]
    assert all(e["payload"]["op_id"] == "op-inst-1" for e in lines)
    done = events.last("progress-done")
    assert done is not None and done["payload"]["op_id"] == "op-inst-1"


def test_install_requirements_emits_done_on_crash(env, monkeypatch):
    from odoo_vite.core import system_check

    def boom(missing, on_line=None, dry_run=False):
        raise RuntimeError("pkexec died")

    monkeypatch.setattr(system_check, "install_requirements", boom)
    api, events = env
    res = api.wizards.install_requirements(["git"], "op-inst-2")
    assert res["ok"] is False  # _safe swallows, message kept
    done = events.last("progress-done")
    assert done is not None and done["payload"]["op_id"] == "op-inst-2"


def _venv_instance(tmp_path, name="venvy"):
    base = tmp_path / name
    (base / "custom_addons").mkdir(parents=True)
    (base / "community").mkdir(parents=True)
    venv = base / "venv"
    return Instance(
        name=name, version="17.0", path=str(base),
        venv_path=str(venv),
        custom_addons_path=str(base / "custom_addons"),
        community_path=str(base / "community"),
        conf_path=str(base / "odoo.conf"), log_path=str(base / "odoo.log"),
        port=8098, db_user="odoo", db_password="odoo",
        primary_db="venv_db", status="stopped", db_created=True,
    ), venv


def test_venv_status_reports_python_state(env, tmp_path):
    api, _events = env
    inst, venv = _venv_instance(tmp_path)
    iid = _register(inst)
    st = api.config.venv_status(iid)
    assert st["python_ok"] is False
    assert st["venv_path"] == str(venv)
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    assert api.config.venv_status(iid)["python_ok"] is True
    assert api.config.venv_status("nope")["python_ok"] is False


def test_rebuild_venv_streams_progress(env, tmp_path, monkeypatch):
    from odoo_vite.core import venv_manager

    def fake_rebuild(instance_id, progress_cb=None, cancel=None,
                     db_path=None):
        progress_cb("removing old venv")
        progress_cb("pip install -r requirements.txt")
        return Result.success(
            message="venv rebuilt", data={"instance_id": instance_id})

    monkeypatch.setattr(venv_manager, "rebuild_venv", fake_rebuild)
    api, events = env
    inst, _venv = _venv_instance(tmp_path, "reby")
    iid = _register(inst)
    res = api.config.rebuild_venv(iid, "op-v1")
    assert res["ok"] is True, res
    assert res["message"] == "venv rebuilt"
    lines = [e for e in events.events if e["kind"] == "progress-line"]
    assert [e["payload"]["line"] for e in lines] == [
        "removing old venv", "pip install -r requirements.txt"]
    assert all(e["payload"]["op_id"] == "op-v1" for e in lines)
    done = events.last("progress-done")
    assert done is not None and done["payload"]["op_id"] == "op-v1"


# ---------------------------------------------------------------- bulk ops


def test_lifecycle_stop_many_streams_progress(env, monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses", lambda *a, **k: [
            {"id": "i1", "name": "One", "status": "running"},
            {"id": "i2", "name": "Two", "status": "stopped"},
        ])
    monkeypatch.setattr(
        "odoo_vite.core.process_manager.stop_instance",
        lambda iid: Result(ok=True, message=f"stopped {iid}"))

    api, events = env
    res = api.lifecycle.stop_many(["i1", "i2"], "op-bulk")

    assert res["ok"] is True
    assert "stopped 1" in res["message"]
    assert "skipped 1 not running" in res["message"]
    lines = [e for e in events.events if e["kind"] == "progress-line"]
    texts = [e["payload"]["line"] for e in lines]
    assert any("Stopping One" in t for t in texts)
    assert any("skipped (not running)" in t for t in texts)
    assert all(e["payload"]["op_id"] == "op-bulk" for e in lines)
    done = events.last("progress-done")
    assert done is not None and done["payload"]["op_id"] == "op-bulk"
    msg = events.last("message")
    assert msg["payload"]["level"] == "info"
    json.dumps(events.events)


def test_lifecycle_start_many_failure_is_error_message(env, monkeypatch):
    from odoo_vite.core import process_manager

    monkeypatch.setattr(
        process_manager, "get_statuses", lambda *a, **k: [
            {"id": "i1", "name": "One", "status": "stopped"},
        ])
    monkeypatch.setattr(
        "odoo_vite.core.process_manager.start_instance",
        lambda *a, **k: Result.failure("venv python missing"))

    api, events = env
    res = api.lifecycle.start_many(["i1"], "op-bulk2")

    assert res["ok"] is False
    assert "venv python missing" in res["message"]
    msg = events.last("message")
    assert msg["payload"]["level"] == "error"
    done = events.last("progress-done")
    assert done is not None and done["payload"]["op_id"] == "op-bulk2"


def test_devtools_detect_editors_facade(env):
    """3.1.0 N2: PATH probe exposed through the facade (UI gates editor buttons)."""

    api, _events = env
    found = api.devtools.detect_editors()
    assert isinstance(found, dict)
    assert set(found) <= {"code", "cursor"}


def test_health_facade(env):
    """3.1.0 C2: health report exposed through the facade for the strip."""

    api, _events = env
    rep = api.app.health("no-such-id")
    assert rep["level"] == "error" and rep["checks"]

    inst = Instance(name="h1", version="17.0")
    iid = _register(inst)
    rep = api.app.health(iid)
    assert rep["instance_id"] == iid
    assert {c["name"] for c in rep["checks"]} == {
        "process", "venv", "postgres", "disk", "log"}
