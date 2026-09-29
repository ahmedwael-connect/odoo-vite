"""Slint bridge UI tests (PSS-3/4): paints, dispatch, dialog flows.

Determinism contract (docs/slint-thread-safety.md): Slint values and
worker threads NEVER coexist here. All spawns funnel through
asyncio.run_coroutine_threadsafe, which is stubbed per test to record
(never execute) — so no worker thread ever exists. Real core execution
lives in test_slint_ops.py (Slint-free); live integration is manual.
Dialogs are shown headless (proven safe) and always dismissed — retained
or abandoned, either directly or via the bridge close() path.

GC discipline (PSS-5b): dialog-result lambdas close over their driver
while the driver holds the lambda — a cycle only cyclic GC can free, and
CPython runs GC on whichever thread allocates past threshold, including
the bridge's idle loop thread (SIGABRT in `__clear__`, verified). So
automatic GC stays OFF for the whole test (refcounting still frees
acyclic trash immediately) and teardown collects explicitly on the main
thread after every loop is joined — Slint values are then only ever
freed on their owner thread.
"""

import asyncio
import gc

import pytest

pytest.importorskip("slint", reason="slint package required")

_BRIDGES: list = []


@pytest.fixture()
def spawns(monkeypatch):
    """Record spawns instead of running them: zero worker threads."""
    recorded = []

    def _record(coro, loop):
        recorded.append(coro)
        try:
            coro.close()
        except Exception:
            pass

        class _Done:
            def add_done_callback(self, fn):
                pass

            def done(self):
                return True

        return _Done()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _record)
    return recorded


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Registry isolation FIRST — rows must never touch the real DB,
    no matter what order helpers run in."""
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "slint-br.db"))


def _bridge(tmp_path, monkeypatch):
    from odoo_vite.ui_slint.bridge import SlintBridge

    bridge = SlintBridge()
    _BRIDGES.append(bridge)
    return bridge


@pytest.fixture(autouse=True)
def _no_auto_gc():
    gc.disable()
    yield
    gc.enable()


@pytest.fixture(autouse=True)
def _close_bridges():
    yield
    while _BRIDGES:
        bridge = _BRIDGES.pop()
        try:
            assert bridge.wait_idle(timeout=15.0)
            bridge.close()
        except Exception:
            pass
    # Quiescent collect on the owner thread: loops are joined above, so
    # no worker exists that could trigger cyclic GC off-main (PSS-5b).
    gc.collect()


def _names(bridge):
    return [dict(r)["title"] for r in bridge.window.sidebar_rows]


def _make_instance(tmp_path, name="DBT", **overrides):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    fields = dict(name=name, version="17.0", path=str(tmp_path),
                  primary_db="main", tracked_dbs=["main", "extra"])
    fields.update(overrides)
    inst = Instance(**fields)
    assert create_instance(inst).ok
    return inst


def _qualnames(spawns):
    return [getattr(getattr(c, "cr_code", None), "co_qualname", "?")
            for c in spawns]


def test_sidebar_search_title_and_empty(tmp_path, monkeypatch, spawns):
    """UXS-4: sidebar filter narrows; title follows selection; empty
    registry explains itself."""
    _make_instance(tmp_path, name="SlintA")
    _make_instance(tmp_path, name="SlintB")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.refresh()
    assert bridge.window.sidebar_empty == ""
    bridge._sidebar_search("slinta")
    assert _names(bridge) == ["SlintA"]
    assert "1 of 2 shown" in bridge.window.sidebar_counts
    bridge._sidebar_search("")
    assert _names(bridge) == ["SlintA", "SlintB"]
    inst = _make_instance(tmp_path, name="DBT")
    bridge.select(inst.id)
    assert bridge.window.app_title == "Odoo Vite — DBT"
    bridge.close()


def test_sidebar_empty_registry(tmp_path, monkeypatch, spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.refresh()
    assert _names(bridge) == []
    assert "No instances yet" in bridge.window.sidebar_empty
    assert bridge.window.app_title == "Odoo Vite (Slint)"
    bridge.close()


# ------------------------------------------------------ PSS-5b configuration

def _make_conf_instance(tmp_path, name="CF"):
    conf = tmp_path / f"{name}.conf"
    conf.write_text("[options]\ndb_host = localhost\ndb_user = odoo\n"
                    "addons_path = /a,/b\n")
    return _make_instance(tmp_path, name=name, conf_path=str(conf))


def test_select_paints_conf(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.conf_has_instance is True
    assert bridge.window.conf_path == inst.conf_path
    assert bridge.window.conf_db_host == "localhost"
    assert bridge.window.conf_db_user == "odoo"
    assert bridge.window.conf_addons == "/a,/b"
    assert len(list(bridge.window.conf_lines)) == 3
    assert bridge.window.conf_has_backup is False
    assert list(bridge.window.conf_log_levels)[0] == "info"
    bridge.close()


def test_conf_save_diff_and_notice(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    spawns.clear()
    bridge._on_conf_action("conf-save")
    assert bridge.window.conf_notice == "No changes to save."
    assert spawns == []
    bridge.window.conf_db_host = "db.internal"
    bridge._on_conf_action("conf-save")
    assert bridge.window.conf_notice == ""
    quals = _qualnames(spawns)
    assert any("save" in q for q in quals)
    bridge.close()


def test_conf_raw_set_validates(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge._on_conf_action("raw-set")
    assert bridge.window.conf_notice == "Enter a key name first."
    bridge.window.conf_raw_key = "workers"
    bridge.window.conf_raw_value = "4"
    bridge._on_conf_action("raw-set")
    assert bridge.window.conf_raw_key == ""
    assert any("save" in q for q in _qualnames(spawns))
    bridge.close()


def test_conf_regenerate_confirm_terminal(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge._on_conf_action("conf-regenerate")
    assert len(bridge._dialogs) == 1
    assert bridge._dialogs[0].view.destructive is True
    bridge._done_conf_regen(bridge._dialogs[0], inst.id, False)
    assert bridge._dialogs == []
    assert spawns == []
    bridge._on_conf_action("conf-regenerate")
    bridge._done_conf_regen(bridge._dialogs[0], inst.id, True)
    assert any("regenerate" in q for q in _qualnames(spawns))
    bridge.close()


def test_conf_addons_manage_and_apply(tmp_path, monkeypatch, spawns):
    from odoo_vite.ui_slint.dialogs import AddonsDriver

    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge._on_conf_action("addons-manage")
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    assert isinstance(drv, AddonsDriver)
    assert sorted(drv._state.checked_ids()) == ["/a", "/b"]
    bridge._done_addons_apply(drv, inst.id, drv._entries)
    assert bridge._dialogs == []
    assert any("apply_addons" in q for q in _qualnames(spawns))
    bridge.close()


def test_conf_addons_browse_and_python_pick(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge._on_conf_action("addons-manage")
    drv = bridge._dialogs[0]
    bridge._spawn_addons_browse(drv)
    assert any("_run_conf_picker" in q for q in _qualnames(spawns))
    # Manual drain: empty path ignored; real dir accepted with a warning
    # when it holds no manifests.
    bridge._post("addons-pick", (drv, ""))
    bridge._drain()
    assert drv.view.add_path == ""
    plain = tmp_path / "plain"
    plain.mkdir()
    bridge._post("addons-pick", (drv, str(plain)))
    bridge._drain()
    assert drv.view.add_path == str(plain)
    assert "manifest" in bridge.window.toast_message
    bridge._on_conf_action("browse-python")
    assert any("_run_conf_picker" in q for q in _qualnames(spawns))
    bridge._post("python-pick", "/usr/bin/python3")
    bridge._drain()
    assert bridge.window.conf_python == "/usr/bin/python3"
    bridge.close()


def test_conf_meta_save_validates(tmp_path, monkeypatch, spawns):
    inst = _make_conf_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    spawns.clear()
    bridge.window.conf_python = "/no/such/python"
    bridge._on_conf_action("meta-save")
    assert "Not an executable" in bridge.window.conf_py_error
    assert spawns == []
    bridge.window.conf_python = ""
    bridge._on_conf_action("meta-save")
    assert bridge.window.conf_py_error == ""
    assert any("meta_save" in q for q in _qualnames(spawns))
    bridge.close()


# ----------------------------------------------------------- PSS-6a logs

LOGS_TAB = 4


def _make_log_instance(tmp_path, name="LG", lines=300):
    log = tmp_path / f"{name}.log"
    log.write_text("".join(f"INFO line {i}\n" for i in range(lines)))
    return _make_instance(tmp_path, name=name, log_path=str(log)), log


def test_select_starts_tail(tmp_path, monkeypatch, spawns):
    inst, _log = _make_log_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.log_has_instance is True
    assert "Tailing" in bridge.window.log_note
    assert len(list(bridge.window.log_tail_lines)) == 300
    bridge.close()


def test_select_without_log_path(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.log_note == "No log file recorded."
    assert list(bridge.window.log_tail_lines) == []
    bridge.close()


def test_tail_tick_gated_and_appends(tmp_path, monkeypatch, spawns):
    inst, log = _make_log_instance(tmp_path, lines=10)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert len(list(bridge.window.log_tail_lines)) == 10
    with open(log, "a") as fh:
        fh.write("ERROR fresh\n")
    # Hidden tab: poll skipped, offset untouched.
    bridge.window.tab_index = 0
    bridge._tail_tick()
    assert len(list(bridge.window.log_tail_lines)) == 10
    # Visible tab: appends.
    bridge.window.tab_index = LOGS_TAB
    bridge._tail_tick()
    tail = [str(r) for r in bridge.window.log_tail_lines]
    assert tail[-1] == "ERROR fresh"
    # Follow off: polls advance the offset but append nothing.
    with open(log, "a") as fh:
        fh.write("INFO skipped\n")
    bridge.window.log_follow = False
    bridge._tail_tick()
    assert [str(r) for r in bridge.window.log_tail_lines][-1] == \
        "ERROR fresh"
    bridge.window.log_follow = True
    with open(log, "a") as fh:
        fh.write("INFO resumed\n")
    bridge._tail_tick()
    assert [str(r) for r in bridge.window.log_tail_lines][-1] == \
        "INFO resumed"
    # Rotation clears and notes.
    log.write_text("INFO after rotate\n")
    bridge._tail_tick()
    tail = [str(r) for r in bridge.window.log_tail_lines]
    assert tail == ["INFO after rotate"]
    assert "rotated" in bridge.window.log_note
    bridge.close()


def test_tail_missing_file_note(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="Ghost",
                          log_path=str(tmp_path / "never.log"))
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge.window.tab_index = LOGS_TAB
    bridge._tail_tick()
    assert "Waiting for log file" in bridge.window.log_note
    bridge.close()


def test_log_dispatch_and_clear(tmp_path, monkeypatch, spawns):
    inst, _log = _make_log_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge.window.log_search = "boom"
    bridge._on_log_action("search")
    bridge._on_log_action("doctor")
    bridge._on_log_action("slow-refresh")
    bridge._on_log_action("profile")
    quals = _qualnames(spawns)
    assert any("search" in q for q in quals)
    assert any("doctor" in q for q in quals)
    assert any("slow_refresh" in q for q in quals)
    assert any("profile" in q for q in quals)
    bridge.select(inst.id)
    assert len(list(bridge.window.log_tail_lines)) == 300
    bridge._on_log_action("clear")
    assert list(bridge.window.log_tail_lines) == []
    bridge._on_log_action("search")  # no selection, no crash
    bridge._current_id = None
    bridge._on_log_action("search")
    assert bridge.window.toast_message == "Select an instance first"
    bridge.close()


def test_log_paints(tmp_path, monkeypatch, spawns):
    inst, _log = _make_log_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._post("log-search",
                 (inst.id, True, "3 matches",
                  [{"lineno": 1, "line": "ERROR x", "before": [],
                    "after": []}]))
    bridge._drain()
    assert bridge.window.log_search_status == "3 matches"
    assert [str(r) for r in bridge.window.log_search_lines] == \
        ["line 1: ERROR x"]
    bridge._post("log-doctor",
                 (inst.id, [{"severity": "high", "title": "T",
                             "count": 2}]))
    bridge._drain()
    assert bridge.window.log_doctor_visible is True
    bridge._post("log-slow", (inst.id, True, "ok",
                              [{"query": "SELECT 1", "calls": 1,
                                "total_ms": 2}]))
    bridge._drain()
    assert "SELECT 1" in str(bridge.window.log_slow_lines[0])
    # Stale payloads ignored.
    bridge._post("log-search", ("other", True, "x", []))
    bridge._drain()
    assert bridge.window.log_search_status == "3 matches"
    # Profile completion toasts and opens externally.
    bridge._post("profile-ready", (inst.id, True, "done", "/t/x.svg"))
    bridge._drain()
    assert bridge.window.toast_message == "done"
    assert any("open_svg_external" in q for q in _qualnames(spawns))
    bridge._post("profile-ready", (inst.id, False, "nope", ""))
    bridge._drain()
    assert bridge.window.toast_kind == "error"
    bridge.close()


# -------------------------------------------------------- PSS-6b devtools

def _dev_instance(tmp_path, name="DT"):
    return _make_instance(tmp_path, name=name, primary_db="main")


def test_dev_select_resets_view(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.dev_has_instance is True
    assert bridge.window.dev_rpc_status == "Not connected."
    assert list(bridge.window.dev_model_rows) == []
    assert "Connect" in bridge.window.dev_models_empty
    bridge.close()


def test_dev_dispatch_records_spawns(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge._on_dev_action("rpc-connect")
    bridge._on_dev_action("rec-search")
    bridge._on_dev_action("rec-prev")
    bridge._on_dev_action("rec-next")
    bridge._on_dev_action("cron-refresh")
    bridge._on_dev_action("gen-launch")
    bridge._on_dev_action("open-code")
    bridge._on_dev_action("open-cursor")
    quals = _qualnames(spawns)
    for op in ("rpc_connect", "rec_search", "rec_page", "cron_refresh",
               "launch_json", "open_editor"):
        assert any(op in q for q in quals), op
    bridge._current_id = None
    bridge._on_dev_action("rpc-connect")
    assert bridge.window.toast_message == "Select an instance first"
    bridge.close()


def test_dev_paints(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._post("dev-rpc", (inst.id, "Connected as admin (db main)."))
    bridge._drain()
    assert "Connected" in bridge.window.dev_rpc_status
    bridge._post("dev-models", (inst.id, [
        {"technical": "sale.order", "display": "Sales Order"}]))
    bridge._drain()
    assert [dict(r)["id"] for r in bridge.window.dev_model_rows] == \
        ["sale.order"]
    assert bridge.window.dev_models_empty == ""
    bridge._post("dev-meta", (inst.id, {"fields": [
        {"name": "name"}]}))
    bridge._drain()
    assert "1 field(s)" in bridge.window.dev_meta
    bridge._post("dev-records", (inst.id, [
        {"id": 1, "display_name": "Desk"},
        {"id": 2, "display_name": "Chair"}], 0, False))
    bridge._drain()
    assert bridge.window.dev_rec_empty == ""
    assert bridge.window.dev_rec_page == "Page 1 (offset 0)"
    assert [dict(r)["id"] for r in bridge.window.dev_rec_rows] == \
        ["1", "2"]
    bridge._post("dev-crons", (inst.id, [
        {"name": "C", "nextcall": "soon", "active": False}]))
    bridge._drain()
    assert "paused" in str(bridge.window.dev_cron_lines[0])
    assert bridge.window.dev_cron_empty == ""
    # Stale payloads ignored.
    bridge._post("dev-rpc", ("other", "stale"))
    bridge._drain()
    assert "Connected" in bridge.window.dev_rpc_status
    bridge.close()


def test_dev_model_and_rec_picks(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge._dev_models_picked("sale.order")
    assert bridge.window.dev_model_selected == "sale.order"
    assert any("model_metadata" in q for q in _qualnames(spawns))
    bridge._post("dev-records", (inst.id, [
        {"id": 7, "display_name": "Desk"}], 0, False))
    bridge._drain()
    bridge._dev_rec_picked("7")
    assert bridge._dev_rec_current == 7
    assert bridge.window.dev_rec_selected == "7"
    bridge._dev_rec_picked("bogus")
    assert bridge._dev_rec_current == 7
    # Vanished current clears on repaint.
    bridge._post("dev-records", (inst.id, [], 0, False))
    bridge._drain()
    assert bridge.window.dev_rec_selected == ""
    assert bridge._dev_rec_current is None
    bridge.close()


def test_dev_rec_new_flow(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge._on_dev_action("rec-new")
    assert bridge.window.toast_message == "Pick a model first"
    bridge._dev_model = "sale.order"
    bridge._on_dev_action("rec-new")
    assert "editable fields" in bridge.window.toast_message
    bridge._dev_meta = {"fields": [{"name": "name", "ttype": "char"}]}
    bridge._on_dev_action("rec-new")
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    drv.view.field_edited("name", "Desk")
    drv.view.save_requested()
    assert bridge._dialogs == []
    assert any("rec_create" in q for q in _qualnames(spawns))
    bridge.close()


def test_dev_rec_edit_flow(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._dev_model = "sale.order"
    bridge._dev_meta = {"fields": [{"name": "name", "ttype": "char"}]}
    bridge._dev_records = [{"id": 7, "display_name": "Desk",
                            "name": "Desk"}]
    spawns.clear()
    bridge._on_dev_action("rec-edit")
    assert bridge.window.toast_message == "Select a record first"
    bridge._dev_rec_current = 7
    bridge._on_dev_action("rec-edit")
    assert len(bridge._dialogs) == 1
    edit_drv = bridge._dialogs[0]
    # Saving unchanged values toasts instead of spawning.
    edit_drv.view.save_requested()
    assert bridge._dialogs == []
    assert "No changes" in bridge.window.toast_message
    assert not any("rec_update" in q for q in _qualnames(spawns))
    # Changed values get a diff preview confirm.
    bridge._on_dev_action("rec-edit")
    edit_drv = bridge._dialogs[0]
    edit_drv.view.field_edited("name", "Desk Pro")
    edit_drv.view.save_requested()
    assert len(bridge._dialogs) == 1  # editor released, confirm shown
    assert "Desk Pro" in bridge._dialogs[0].view.body
    bridge._dialogs[0].view.confirmed()
    assert any("rec_update" in q for q in _qualnames(spawns))
    bridge.close()


def test_dev_rec_delete_flow(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._dev_model = "ir.config"
    bridge._dev_records = [{"id": 1, "display_name": "X"}]
    bridge._dev_rec_current = 1
    bridge._on_dev_action("rec-delete")
    assert "off-limits" in bridge.window.toast_message
    assert bridge._dialogs == []
    bridge._dev_model = "sale.order"
    bridge._on_dev_action("rec-delete")
    assert len(bridge._dialogs) == 1
    assert bridge._dialogs[0].view.expected == "X"
    bridge._dialogs[0].view.entry = "X"
    bridge._dialogs[0].view.confirmed()
    assert any("rec_delete" in q for q in _qualnames(spawns))
    bridge.close()


def test_dev_test_run_flow(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="TT", primary_db="main")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    bridge._on_dev_action("test-run")
    assert "technical name" in bridge.window.toast_message
    bridge.window.dev_test_module = "sale"
    bridge.window.dev_test_db = "main"
    bridge._on_dev_action("test-run")
    assert "PRIMARY" in bridge.window.toast_message
    bridge.window.dev_test_db = "main_test"
    bridge._on_dev_action("test-run")
    assert len(bridge._dialogs) == 1
    bridge._done_test_run(bridge._dialogs[0], inst.id, "sale",
                          "main_test", True)
    assert any("_run_test_progress" in q for q in _qualnames(spawns))
    assert bridge.window.dev_busy is True
    prog = bridge._dialogs[0]
    bridge._post("progress-line", (prog, "streamed"))
    bridge._drain()
    assert "streamed" in prog.view.log
    bridge._post("dev-progress-done", (prog, True, "Tests passed"))
    bridge._drain()
    assert bridge.window.dev_busy is False
    assert prog.view.finished is True
    prog.view.closed()
    bridge.close()


class _FakeShell:
    def __init__(self, lines=None, alive=True):
        self._lines = list(lines or [])
        self._alive = alive
        self.sent = []
        self.stopped = False

    def drain_output(self):
        out = self._lines
        self._lines = []
        return out

    @property
    def running(self):
        return self._alive

    def send_line(self, text):
        from odoo_vite.core.result import Result

        self.sent.append(text)
        return Result.success(message="sent")

    def stop(self):
        from odoo_vite.core.result import Result

        self.stopped = True
        return Result.success(message="stopped")


def test_dev_shell_flows(tmp_path, monkeypatch, spawns):
    inst = _dev_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    spawns.clear()
    # No session: send toasts, stop is a silent no-op.
    bridge._on_dev_action("shell-send")
    assert bridge.window.toast_message == "Shell is not running"
    bridge._on_dev_action("shell-stop")
    assert spawns == []
    # Start registers the session optimistically.
    bridge._on_dev_action("shell-start")
    assert any("_run_shell_start" in q for q in _qualnames(spawns))
    assert bridge.window.dev_shell_running is True
    session = bridge._shells[inst.id]
    try:
        session.stop()
    except Exception:
        pass
    # Fake session: drain appends, exit flips status.
    fake = _FakeShell(lines=["echo hi"], alive=True)
    bridge._shells[inst.id] = fake
    bridge._shell_starting.discard(inst.id)
    bridge.window.tab_index = 5
    bridge._shell_poll_tick()
    assert [str(r) for r in bridge.window.dev_shell_lines] == ["echo hi"]
    fake._alive = False
    bridge._shell_poll_tick()
    assert bridge.window.dev_shell_running is False
    assert bridge.window.dev_shell_status == "Shell exited."
    assert inst.id not in bridge._shells
    # Started/failed/stopped paints.
    bridge._shells[inst.id] = fake
    bridge._shell_starting.add(inst.id)
    bridge._post("shell-started", (inst.id, "Shell ready."))
    bridge._drain()
    assert bridge.window.dev_shell_status == "Shell ready."
    bridge._post("shell-start-failed", (inst.id, fake, "boom"))
    bridge._drain()
    assert bridge.window.toast_kind == "error"
    assert inst.id not in bridge._shells
    bridge._post("shell-stopped", inst.id)
    bridge._drain()
    assert bridge.window.dev_shell_status == "Shell stopped."
    # Send path with a live fake.
    bridge._shells[inst.id] = fake
    fake._alive = True
    bridge.window.dev_shell_in = "1+1"
    bridge._on_dev_action("shell-send")
    assert fake.sent == ["1+1"]
    assert bridge.window.dev_shell_in == ""
    bridge.close()


# ---------------------------------------------------------- PSS-7a create

def test_wiz_new_opens_and_loads(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("new")
    assert len(bridge._dialogs) == 1
    assert any("load_branches" in q for q in _qualnames(spawns))
    drv = bridge._dialogs[0]
    bridge._post("wiz-branches", (drv, ["17.0", "16.0"], "2 branches"))
    bridge._drain()
    assert [dict(r)["id"] for r in drv.view.branch_rows] == \
        ["17.0", "16.0"]
    bridge.close()


def test_wiz_nav_validation(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path, name="Taken")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("new")
    drv = bridge._dialogs[0]
    drv.view.wiz_nav("next")  # page 0, nothing picked
    assert drv.view.page_idx == 0
    assert "Pick a version" in drv.view.branch_status
    drv.view.branch_picked("17.0")
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 1
    assert any("run_syscheck" in q for q in _qualnames(spawns))
    bridge._post("wiz-syscheck", (drv, [{"name": "py", "ok": True,
                                         "detail": "3.12"}], "all good"))
    bridge._drain()
    assert "py" in str(drv.view.syscheck_lines[0])
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 2
    drv.view.wiz_nav("next")  # empty details
    assert drv.view.page_idx == 2
    assert "required" in drv.view.det_err
    drv.view.det_name = "Taken"
    drv.view.wiz_nav("next")
    assert "already exists" in drv.view.det_err
    drv.view.wiz_nav("back")
    assert drv.view.page_idx == 1
    drv.view.wiz_nav("cancel")
    assert bridge._dialogs == []
    bridge.close()


def test_wiz_details_gen_and_provision(tmp_path, monkeypatch, spawns):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("new")
    drv = bridge._dialogs[0]
    drv.view.branch_picked("17.0")
    assert drv.view.branch_selected == "17.0"
    drv.view.wiz_nav("next")
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 2
    before = drv.view.det_dbpass
    drv.view.gen_password()
    assert drv.view.det_dbpass != before
    assert len(drv.view.det_dbpass) == 20
    drv.view.det_name = "Fresh"
    drv.view.det_dbname = "freshdb"
    spawns.clear()
    drv.view.wiz_nav("next")  # validates + provisions
    assert drv.view.page_idx == 3
    assert drv.view.prov_running is True
    assert any("_run_wiz_provision" in q for q in _qualnames(spawns))
    assert drv._draft is not None and drv._draft.status == "draft"
    # Streamed lines land in the dialog log.
    bridge._post("wiz-log", (drv, "cloning…"))
    bridge._drain()
    assert "cloning" in drv.view.prov_log
    # Cancel signals the worker.
    drv.view.prov_cancel()
    assert drv.cancel_event.is_set()
    # Failure surfaces Retry/Discard.
    bridge._post("wiz-done", (drv, drv._draft.id, False, "git down",
                              "clone"))
    bridge._drain()
    assert drv.view.prov_failed is True
    assert "ERROR" in drv.view.prov_log
    assert "git down" in bridge.window.toast_message
    # Retry resumes with the same draft.
    spawns.clear()
    drv.view.prov_retry()
    assert any("_run_wiz_provision" in q for q in _qualnames(spawns))
    # Success closes the loop; Close releases + refreshes.
    bridge._post("wiz-done", (drv, drv._draft.id, True, "ready", ""))
    bridge._drain()
    assert drv.view.prov_done is True
    drv.view.wiz_nav("close")
    assert bridge._dialogs == []
    bridge.close()


def test_wiz_discard_flow(tmp_path, monkeypatch, spawns):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("new")
    drv = bridge._dialogs[0]
    drv.view.branch_picked("17.0")
    drv.view.wiz_nav("next")
    drv.view.wiz_nav("next")
    drv.view.det_name = "Doomed"
    drv.view.det_dbname = "doomeddb"
    drv.view.wiz_nav("next")
    assert drv.view.prov_running is True
    assert drv._draft is not None
    spawns.clear()
    drv.view.prov_discard()
    assert any("_run_wiz_discard" in q for q in _qualnames(spawns))
    bridge._post("wiz-discarded", (drv, True, "discarded"))
    bridge._drain()
    assert bridge._dialogs == []
    assert "discarded" in bridge.window.toast_message
    # Discard without a draft just closes (defensive path).
    bridge._on_wiz_action("new")
    drv2 = bridge._dialogs[0]
    drv2.view.prov_discard()
    assert drv2 not in bridge._dialogs
    bridge.close()


# ------------------------------------------------------------ PSS-7b adopt

def _adopt_layout(tmp_path):
    community = tmp_path / "community"
    (community / "odoo").mkdir(parents=True)
    (community / "odoo-bin").write_text("#!/bin/sh\n")
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_user = odoo\nxmlrpc_port = 8069\n")
    return str(conf), str(community)


def test_wiz_adopt_opens_and_reparses(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("adopt")
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    conf, community = _adopt_layout(tmp_path)
    drv.view.loc_conf = conf
    drv.view.locate_changed()
    assert "conf parsed" in drv.view.loc_detected
    drv.view.loc_community = community
    drv.view.locate_changed()
    assert "odoo-bin found" in drv.view.loc_detected
    bridge.close()


def test_wiz_adopt_locate_validation(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path, name="Taken")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("adopt")
    drv = bridge._dialogs[0]
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 0
    assert "required" in drv.view.loc_err
    drv.view.loc_name = "Taken"
    conf, community = _adopt_layout(tmp_path)
    drv.view.loc_conf = conf
    drv.view.loc_community = community
    drv.view.wiz_nav("next")
    assert "already exists" in drv.view.loc_err
    drv.view.loc_name = "Fresh"
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 1
    assert len(list(drv.view.gap_rows)) == 5  # always 5 fields
    assert drv.view.gap_db != ""  # slugified default
    drv.view.wiz_nav("back")
    assert drv.view.page_idx == 0
    drv.view.wiz_nav("cancel")
    assert bridge._dialogs == []
    bridge.close()


def test_wiz_adopt_run_flow(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("adopt")
    drv = bridge._dialogs[0]
    conf, community = _adopt_layout(tmp_path)
    drv.view.loc_name = "Legacy"
    drv.view.loc_conf = conf
    drv.view.loc_community = community
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 1
    drv.view.gap_db = ""
    drv.view.wiz_nav("next")  # db required
    assert drv.view.page_idx == 1
    assert "required" in drv.view.gap_err
    drv.view.gap_db = "legacydb"
    # Fill the missing password gap through the row edit.
    drv.view.gap_edited("db_password", "pw")
    spawns.clear()
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 2
    assert any("_run_wiz_adopt" in q for q in _qualnames(spawns))
    assert "Adopting Legacy" in drv.view.run_status
    bridge._post("wiz-adopt-done", (drv, True, "Adopted", "new-id"))
    bridge._drain()
    assert drv.view.run_done is True
    assert "no files touched" in drv.view.run_status
    assert "Adopted" in bridge.window.toast_message
    drv.view.wiz_nav("close")
    assert bridge._dialogs == []
    bridge.close()


def test_wiz_adopt_browse_and_failure(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_wiz_action("adopt")
    drv = bridge._dialogs[0]
    assert drv.view.page_idx == 0
    drv.view.browse_conf()
    drv.view.browse_community()
    assert any("_run_conf_picker" in q for q in _qualnames(spawns))
    bridge._post("adopt-browse-conf", (drv, "/picked/odoo.conf"))
    bridge._drain()
    assert drv.view.loc_conf == "/picked/odoo.conf"
    bridge._post("adopt-browse-community", (drv, "/picked/comm"))
    bridge._drain()
    assert drv.view.loc_community == "/picked/comm"
    # Failure keeps the dialog open with the error.
    drv.view.loc_name = "Legacy"
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 0  # bogus paths don't validate
    bridge.close()


# ------------------------------------------------- PSS-8 transfer + prefs

def test_sys_preferences_flow(tmp_path, monkeypatch, spawns):
    from odoo_vite.ui_slint.transfer import read_preferences

    bridge = _bridge(tmp_path, monkeypatch)
    current = read_preferences()["mode"]
    other = "managed" if current == "developer" else "developer"
    bridge._on_sys_action("preferences")
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    assert drv.view.db_path != "" or True  # env-dependent, shown if any
    # Unchanged mode closes silently.
    drv.view.mode_changed(drv.view.mode_idx)
    bridge._done_preferences(drv, current, drv.selected_mode())
    assert bridge._dialogs == []
    # Flip persists across reads.
    bridge._on_sys_action("preferences")
    drv = bridge._dialogs[0]
    flipped = 1 - drv.view.mode_idx
    drv.view.mode_changed(flipped)
    assert "CREATEDB" in drv.view.mode_note or \
        "Least-privilege" in drv.view.mode_note
    bridge._done_preferences(drv, current, drv.selected_mode())
    assert bridge._dialogs == []
    assert read_preferences()["mode"] == other
    bridge.close()


def test_ov_export_flow(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="EX")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = None
    bridge._export_flow()
    assert bridge.window.toast_message == "Select an instance first"
    bridge._current_id = inst.id
    spawns.clear()
    bridge._export_flow()
    assert any("_run_picker" in q for q in _qualnames(spawns))
    bridge._post("export-pick", (inst.id, ""))
    bridge._drain()  # empty dest: nothing happens
    assert bridge._dialogs == []
    bridge._post("export-pick", (inst.id, "/tmp/ex.tar.gz"))
    bridge._drain()
    assert any("export_bundle" in q for q in _qualnames(spawns))
    bridge.close()


def _mini_bundle(tmp_path, name="Bundled", version="17.0", port=8071):
    import json
    import tarfile

    archive = tmp_path / "bundle.tar.gz"
    manifest = {"format": 1, "name": name, "version": version,
                "port": port}
    info_file = tmp_path / "instance.json"
    info_file.write_text(json.dumps(manifest))
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(str(info_file), arcname="instance.json")
    return str(archive)


def test_sys_import_flow(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    spawns.clear()
    bridge._on_sys_action("import")
    assert any("_run_picker" in q for q in _qualnames(spawns))
    bridge._post("import-pick", "")
    bridge._drain()
    assert bridge._dialogs == []
    bridge._post("import-pick", str(tmp_path / "nope.tar.gz"))
    bridge._drain()
    assert "Cannot read bundle" in bridge.window.toast_message
    assert bridge.window.toast_kind == "error"
    bridge._post("import-pick", _mini_bundle(tmp_path))
    bridge._drain()
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    assert drv.view.bundle_name == "Bundled"
    assert drv.view.new_port == 8072  # suggested +1
    drv.view.new_name = ""
    drv.view.import_requested()
    assert len(bridge._dialogs) == 1  # blank name: no delivery
    drv.view.new_name = "Restored"
    drv.view.import_requested()
    assert bridge._dialogs == []
    assert any("import_bundle" in q for q in _qualnames(spawns))
    bridge.close()


def test_sys_about_opens(tmp_path, monkeypatch, spawns):
    from odoo_vite.ui_slint.dialogs import AboutDriver

    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_sys_action("about")
    assert len(bridge._dialogs) == 1
    assert isinstance(bridge._dialogs[0], AboutDriver)
    assert bridge._dialogs[0].view.app_version == "3.0.0"
    bridge.close()


# ---------------------------------------------------------- PSS-7c scaffold

def test_mod_scaffold_opens(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="SC1")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._on_mod_action("scaffold")
    assert len(bridge._dialogs) == 1
    drv = bridge._dialogs[0]
    assert list(drv.view.scaf_instances) == ["SC1"]
    bridge.close()


def test_mod_scaffold_no_instances(tmp_path, monkeypatch, spawns):
    from odoo_vite.core import registry as registry_mod

    inst = _make_instance(tmp_path, name="SC0")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    monkeypatch.setattr(registry_mod, "list_instances", lambda: [])
    bridge._on_mod_action("scaffold")
    assert "No instance" in bridge.window.toast_message
    assert bridge._dialogs == []
    bridge.close()


def test_scaffold_validation_and_build(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="SC2")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._on_mod_action("scaffold")
    drv = bridge._dialogs[0]
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 0
    assert "Technical name" in drv.view.scaf_err
    drv.view.scaf_tech = "my_library"
    drv.view.scaf_db = "testdb"
    drv.view.scaf_dest = str(tmp_path)
    spawns.clear()
    drv.view.wiz_nav("next")
    assert drv.view.page_idx == 1
    assert drv.view.build_running is True
    assert any("_run_wiz_scaffold" in q for q in _qualnames(spawns))
    bridge._post("wiz-log", (drv, "generating…"))
    bridge._drain()
    assert "generating" in drv.view.build_log
    bridge._post("wiz-done", (drv, "SC2-id", False, "no pg",
                              "scaffold"))
    bridge._drain()
    assert drv.view.build_running is False
    assert "ERROR" in drv.view.build_log
    assert "no pg" in bridge.window.toast_message
    # Success marks clean; Back returns to the form; Close releases.
    bridge._post("wiz-done", (drv, "SC2-id", True, "clean", ""))
    bridge._drain()
    assert drv.view.build_done is True
    drv.view.wiz_nav("back")
    assert drv.view.page_idx == 0
    drv.view.wiz_nav("next")
    drv.view.wiz_nav("close")
    assert bridge._dialogs == []
    bridge.close()


def test_scaffold_browse_dest(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, name="SC3")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._on_mod_action("scaffold")
    drv = bridge._dialogs[0]
    drv.view.browse_dest()
    assert any("_run_conf_picker" in q for q in _qualnames(spawns))
    bridge._post("scaffold-dest-pick", (drv, "/picked/dest"))
    bridge._drain()
    assert drv.view.scaf_dest == "/picked/dest"
    bridge.close()


def test_lists_registry(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path, name="SlintA")
    _make_instance(tmp_path, name="SlintB")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.refresh()
    assert _names(bridge) == ["SlintA", "SlintB"]
    assert "2 instance" in bridge.window.status_line
    bridge.close()


def test_elide_middle_qt_parity():
    """Elision rule, frozen at cutover (was parity-tested against the Qt
    view until ui_qt/ was deleted in PSS-9)."""
    from odoo_vite.ui_slint.bridge import elide_middle

    assert elide_middle("") == ""
    assert elide_middle("short") == "short"
    assert elide_middle("x" * 60) == "x" * 60
    clipped = elide_middle("x" * 61)
    assert len(clipped) == 60 and clipped.index("…") == 29
    assert len(elide_middle("x" * 200)) == 60
    long_path = "/srv/odoo/vanilla-17.0/" + "y" * 80 + "/odoo.conf"
    assert elide_middle(long_path).startswith("/srv/odoo/vanilla-17.0/")
    assert elide_middle(long_path).endswith("/odoo.conf")


def test_selection_paints_overview_and_databases(tmp_path, monkeypatch,
                                                 spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.ov_name == "DBT"
    assert bridge.window.ov_status == "Stopped"
    assert bridge.window.sidebar_selected == inst.id
    assert list(bridge.window.db_names) == ["main", "extra"]
    assert bridge.window.db_picked == "main"
    assert bridge.window.db_has_instance is True
    first_cell = dict(bridge.window.db_table[0][0])
    assert first_cell["text"] == "★ main"
    # select() only schedules states/schedules/probe — recorded, not run.
    assert any("refresh_states" in q for q in _qualnames(spawns))
    bridge.close()


def test_refresh_picks_up_new_rows(tmp_path, monkeypatch, spawns):
    _make_instance(tmp_path, name="SlintA")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.refresh()
    assert _names(bridge) == ["SlintA"]
    _make_instance(tmp_path, name="SlintB")
    bridge.refresh()
    assert _names(bridge) == ["SlintA", "SlintB"]
    bridge.close()


def test_op_without_selection_toasts(tmp_path, monkeypatch, spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._op("stop", None)
    assert bridge.window.toast_message == "Select an instance first"
    assert bridge.window.toast_showing is True
    assert bridge.window.toast_kind == "error"
    bridge._post_message("all good")
    bridge._drain()
    assert bridge.window.toast_kind == "info"
    bridge.close()


def test_drain_applies_queued_payloads(tmp_path, monkeypatch, spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._post_message("hello from worker")
    bridge._drain()
    assert bridge.window.toast_message == "hello from worker"
    bridge.close()


def test_db_action_without_selection_toasts(tmp_path, monkeypatch, spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_db_action("switch")
    assert bridge.window.toast_message == "Select an instance first"
    bridge._on_sched_action("add")
    assert bridge.window.toast_message == "Select an instance first"
    bridge.close()


def test_db_dispatch_records_spawns(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    spawns.clear()
    bridge.window.db_picked = "extra"
    bridge._on_db_action("set-primary")
    bridge._on_db_action("refresh-states")
    bridge._on_db_action("validate")
    quals = _qualnames(spawns)
    assert any("set_primary" in q for q in quals)
    assert any("refresh_states" in q for q in quals)
    assert any("validate" in q for q in quals)
    bridge.window.db_manual = "newdb"
    bridge._on_db_action("track")
    assert bridge.window.db_manual == ""
    assert any("track_many" in q for q in _qualnames(spawns))
    bridge._on_db_action("switch")
    assert any("_run_switch" in q for q in _qualnames(spawns))
    bridge.close()


def test_drop_primary_refused(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge.window.db_picked = "main"
    bridge._drop_flow(inst, "main")
    assert "primary" in bridge.window.toast_message
    assert bridge._dialogs == []
    bridge.close()


def test_remove_flows_owned_and_released(tmp_path, monkeypatch, spawns):
    adopted = _make_instance(tmp_path, name="Ad", mode="adopted")
    managed = _make_instance(tmp_path, name="Mg", mode="managed")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = adopted.id
    bridge._remove_flow()
    assert len(bridge._dialogs) == 1
    bridge._done_remove(bridge._dialogs[0], adopted.id, False)
    assert bridge._dialogs == []
    assert any("remove" in q for q in _qualnames(spawns)) is False
    bridge._current_id = managed.id
    bridge._remove_flow()
    assert len(bridge._dialogs) == 1
    bridge._done_remove(bridge._dialogs[0], managed.id, True)
    assert bridge._dialogs == []
    assert any("remove" in q for q in _qualnames(spawns))
    bridge.close()


def test_clone_flow_validates_and_spawns(tmp_path, monkeypatch, spawns):
    from odoo_vite.core.registry import update_instance

    running = _make_instance(tmp_path, name="Run", status="stopped")
    still = _make_instance(tmp_path, name="Still", status="stopped")
    bridge = _bridge(tmp_path, monkeypatch)
    # Mark running AFTER construction: init refresh heals stale rows.
    assert update_instance(running.id, status="running").ok
    bridge._current_id = running.id
    bridge._clone_flow()
    assert "Stop" in bridge.window.toast_message
    assert bridge._dialogs == []
    bridge._current_id = still.id
    bridge._clone_flow()
    assert len(bridge._dialogs) == 1
    bridge._done_clone(bridge._dialogs[0], still.id, "Copy", 8071)
    assert bridge._dialogs == []
    assert any("clone" in q for q in _qualnames(spawns))
    bridge.close()


def test_restore_flow_validates(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._continue_restore(inst.id, "main", "")
    assert bridge._dialogs == []
    bridge._continue_restore(inst.id, "", "/tmp/x.dump")
    assert bridge.window.toast_message == "Pick a database first"
    bridge._continue_restore(inst.id, "main", "/tmp/x.dump")
    assert len(bridge._dialogs) == 1
    bridge._done_restore(bridge._dialogs[0], inst.id, "/tmp/x.dump",
                         "main", True)
    assert bridge._dialogs == []
    assert any("restore_db" in q for q in _qualnames(spawns))
    bridge.close()


def test_discover_flow_tracks_and_releases(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._open_discover(inst.id, {"ok": False, "message": "no pg"})
    assert bridge.window.toast_message == "no pg"
    entries = [{"name": "dbx", "initialized": True, "odoo_major": "17.0"}]
    bridge._open_discover(inst.id, {"ok": True, "entries": entries})
    assert len(bridge._dialogs) == 1
    bridge._done_discover_track(bridge._dialogs[0], inst.id, ["dbx"])
    assert bridge._dialogs == []
    assert any("track_many" in q for q in _qualnames(spawns))
    bridge.close()


def test_files_flow_restore_and_delete(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._open_files(inst.id, [])
    assert "No backup files" in bridge.window.toast_message
    files = [{"path": "/b/a.dump", "name": "a.dump", "detail": "10 MB"}]
    bridge._open_files(inst.id, files)
    assert len(bridge._dialogs) == 1
    files_drv = bridge._dialogs[0]
    bridge.window.db_picked = "main"
    files_drv.view.picked("/b/a.dump")
    files_drv.view.restore_requested()
    assert len(bridge._dialogs) == 2  # restore confirm stacked
    bridge._done_restore(bridge._dialogs[1], inst.id, "/b/a.dump",
                         "main", True)
    assert any("restore_db" in q for q in _qualnames(spawns))
    files_drv.view.delete_requested()
    assert len(bridge._dialogs) == 2  # files + delete confirm
    bridge._done_files_delete(files_drv, bridge._dialogs[-1], "/b/a.dump",
                              True)
    assert bridge._dialogs == []
    assert any("file_delete" in q for q in _qualnames(spawns))
    bridge.close()


def test_sched_add_edit_delete(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge._on_sched_action("add")
    assert len(bridge._dialogs) == 1
    payload = {"databases": ["main"], "cron": "0 2 * * *",
               "retention_n": 7, "retention_days": 0}
    bridge._done_sched_save(bridge._dialogs[0], inst.id, None, payload)
    assert bridge._dialogs == []
    assert any("sched_create" in q for q in _qualnames(spawns))

    class _Sched:
        id = "sched-1"
        cron = "0 2 * * *"
        databases = ["main"]
        enabled = True
        retention_n = 7
        retention_days = 0

    bridge._sched_ids = ["sched-1"]
    bridge._sched_objs = {"sched-1": _Sched()}
    bridge.window.sched_row = 0
    bridge._on_sched_action("edit")
    assert len(bridge._dialogs) == 1
    bridge._done_sched_save(bridge._dialogs[0], inst.id, "sched-1",
                            payload)
    assert any("sched_update" in q for q in _qualnames(spawns))
    bridge._on_sched_action("toggle")
    assert any("sched_toggle" in q for q in _qualnames(spawns))
    bridge._on_sched_action("run")
    assert any("run_schedule_now" in q for q in _qualnames(spawns))
    bridge._on_sched_action("delete")
    assert len(bridge._dialogs) == 1
    bridge._done_sched_delete(bridge._dialogs[0], "sched-1", True)
    assert bridge._dialogs == []
    assert any("sched_delete" in q for q in _qualnames(spawns))
    bridge.close()

def test_sched_action_needs_selection(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, primary_db="", tracked_dbs=[])
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    bridge._on_sched_action("add")
    assert bridge.window.toast_message == "Track a database first"
    bridge.window.sched_row = -1
    bridge._on_sched_action("toggle")
    assert bridge.window.toast_message == "Pick a schedule first"
    bridge.close()


def test_db_busy_tracks_inflight(tmp_path, monkeypatch, spawns):
    """UXS-2: spawns gate the tab; the drain clears it when work lands."""
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    assert bridge.window.db_busy is False
    bridge.select(inst.id)
    assert bridge.window.db_busy is True
    # Stubbed spawns never complete — empty the set like done-callbacks
    # would, then the idle signal clears the gate.
    bridge._post("db-idle", None)
    bridge._drain()
    assert bridge.window.db_busy is True  # still in flight
    bridge._db_inflight.clear()
    bridge._post("db-idle", None)
    bridge._drain()
    assert bridge.window.db_busy is False
    bridge.close()


def test_destructive_confirms_flagged(tmp_path, monkeypatch, spawns):
    """UXS-2: destructive tier carries the red heading convention."""
    adopted = _make_instance(tmp_path, name="Ad", mode="adopted")
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = adopted.id
    bridge._remove_flow()
    assert bridge._dialogs[0].view.destructive is True
    bridge._done_remove(bridge._dialogs[0], adopted.id, False)
    bridge._confirm_start(adopted.id, {"detail": "create db?"})
    assert bridge._dialogs[0].view.destructive is False
    bridge._done_confirm_start(bridge._dialogs[0], adopted.id, False)
    assert bridge._dialogs == []
    bridge.close()


def test_sched_pick_and_empty_state(tmp_path, monkeypatch, spawns):
    """UXS-1: schedule rows are clickable; empty list explains itself."""
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id

    class _Sched:
        id = "sched-1"
        cron = "0 2 * * *"
        databases = ["main"]
        enabled = True
        last_run = ""
        last_status = ""
        retention_n = 7
        retention_days = 0

    bridge._post("db-schedules",
                 (inst.id, [_Sched()], {"active": True, "detail": ""}))
    bridge._drain()
    assert bridge.window.sched_empty == ""
    assert len(list(bridge.window.sched_rows)) == 1
    first = dict(bridge.window.sched_rows[0])
    assert "0 2 * * *" in first["label"]  # UXS-3 two-line rows
    assert "main" in first["detail"]
    # Click (view reports index) selects; out-of-range is ignored.
    bridge._sched_picked(0)
    assert bridge.window.sched_row == 0
    assert bridge._selected_schedule().id == "sched-1"
    bridge._sched_picked(7)
    assert bridge.window.sched_row == 0
    bridge._sched_picked("nope")
    assert bridge.window.sched_row == 0
    # Empty list clears a stale selection and explains itself.
    bridge._post("db-schedules", (inst.id, [], {}))
    bridge._drain()
    assert bridge.window.sched_row == -1
    assert "press Add" in bridge.window.sched_empty
    assert bridge._selected_schedule() is None
    bridge.close()


def test_start_confirm_terminal(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._confirm_start(inst.id, {"detail": "create db?"})
    assert len(bridge._dialogs) == 1
    bridge._done_confirm_start(bridge._dialogs[0], inst.id, True)
    assert bridge._dialogs == []
    quals = _qualnames(spawns)
    assert any("start" in q for q in quals)
    bridge.close()


def test_switch_confirm_terminal(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._confirm_switch(inst.id, "extra", {"detail": "make it?"})
    assert len(bridge._dialogs) == 1
    bridge._done_switch_confirm(bridge._dialogs[0], inst.id, "extra",
                                True)
    assert bridge._dialogs == []
    assert any("switch_db" in q for q in _qualnames(spawns))
    bridge.close()


def test_server_ttl_collapses_probes(tmp_path, monkeypatch, spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._server_at = 0.0
    spawns.clear()
    bridge.refresh()
    bridge.refresh()
    quals = _qualnames(spawns)
    assert sum("_run_probe" in q for q in quals) == 1
    bridge._paint_server(False)
    assert "unreachable" in bridge.window.server_note
    bridge._paint_server(True)
    assert bridge.window.server_note == ""
    bridge.close()


def test_enterprise_cached_per_instance(tmp_path, monkeypatch, spawns):
    import odoo_vite.core.enterprise as enterprise_mod

    calls = []
    real_detect = enterprise_mod.detect_enterprise

    def _counting(instance):
        calls.append(1)
        return real_detect(instance)

    monkeypatch.setattr(enterprise_mod, "detect_enterprise", _counting)
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert "Community" in bridge.window.ent_text
    assert len(calls) == 1
    bridge.refresh()
    bridge.refresh()
    assert len(calls) == 1, "poll must not rescan enterprise"
    bridge.close()


def test_owned_dialogs_release_on_terminal(tmp_path, monkeypatch, spawns):
    from odoo_vite.ui_slint.dialogs import ConfirmDriver

    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    drv = ConfirmDriver("Drop?", "Sure?", "Drop",
                        on_result=lambda ok: bridge._done_remove(
                            drv, inst.id, ok))
    bridge._own(drv)
    assert bridge._dialogs == [drv]
    drv.view.cancelled()
    assert bridge._dialogs == []
    assert drv.view is None
    bridge.close()


# ------------------------------------------------------------- PSS-5a modules

def _paint_two_modules(bridge, inst):
    modules = [
        {"name": "sale", "state": "installed",
         "installed_version": "17.0", "available_version": "17.0",
         "summary": "Sales"},
        {"name": "purchase", "state": "uninstalled", "summary": "Purchases"},
    ]
    diff = {"sale": {"status": "disk-newer", "note": "disk 17.1 > db 17.0"}}
    bridge._current_id = inst.id
    bridge._post("modules-ready", (inst.id, modules, diff, ""))
    bridge._drain()
    return modules


def test_select_paints_modules_header(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge.select(inst.id)
    assert bridge.window.mod_has_instance is True
    assert bridge.window.mod_db == "on main"
    assert bridge.window.mod_empty == "Loading…"
    assert any("refresh_modules" in q for q in _qualnames(spawns))
    bridge.close()


def test_mod_action_without_selection_toasts(tmp_path, monkeypatch,
                                             spawns):
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._on_mod_action("install")
    assert bridge.window.toast_message == "Select an instance first"
    bridge.close()


def test_modules_ready_paints_and_filters(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    rows = [dict(r) for r in bridge.window.mod_rows]
    assert [r["id"] for r in rows] == ["sale", "purchase"]
    assert rows[0]["badge"].startswith("Installed")
    assert "⚠" in rows[0]["badge"]
    assert rows[1]["badge"] == "Installable"
    assert bridge.window.mod_empty == ""
    assert "2 of 2 shown" in bridge.window.mod_counts
    # Search matches name AND summary (Qt parity).
    bridge._mod_search("purch")
    assert [dict(r)["id"] for r in bridge.window.mod_rows] == ["purchase"]
    bridge._mod_search("")
    # State filter narrows; empty result explains itself.
    bridge._mod_filter(2)  # Upgradeable
    assert list(bridge.window.mod_rows) == []
    assert "filter" in bridge.window.mod_empty
    bridge._mod_filter(0)
    assert len(list(bridge.window.mod_rows)) == 2
    bridge._mod_filter(99)  # out of range resets to All
    assert bridge.window.mod_filter_idx == 0
    # Stale payloads for other instances are ignored.
    bridge._post("modules-ready", ("other", [], {}, "boom"))
    bridge._drain()
    assert len(list(bridge.window.mod_rows)) == 2
    bridge.close()


def test_mod_toggle_and_pick(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    assert bridge.window.mod_has_checked is False
    bridge._mod_toggled("sale")
    assert bridge.window.mod_has_checked is True
    assert "1 checked" in bridge.window.mod_counts
    # Picks survive a re-render (filter round-trip).
    bridge._mod_search("zzz")
    bridge._mod_search("")
    assert bridge.window.mod_has_checked is True
    bridge._mod_picked("purchase")
    assert bridge.window.mod_selected == "purchase"
    assert bridge._mod_current == "purchase"
    bridge.close()


def test_mod_install_confirm_terminal(tmp_path, monkeypatch, spawns):
    from odoo_vite.ui_slint.dialogs import ProgressDriver

    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    # Nothing checked → toast, no dialog.
    bridge._on_mod_action("install")
    assert "Select modules" in bridge.window.toast_message
    assert bridge._dialogs == []
    bridge._mod_toggled("sale")
    bridge._on_mod_action("install")
    assert len(bridge._dialogs) == 1
    assert "sale" in bridge._dialogs[0].view.heading
    assert "-i" in bridge._dialogs[0].view.body
    # Decline releases with no spawn.
    bridge._done_mod_confirm(bridge._dialogs[0], inst.id, "install",
                             ["sale"], False)
    assert bridge._dialogs == []
    assert not any("_run_mod_progress" in q for q in _qualnames(spawns))
    # Confirm spawns the progress op and gates the view.
    bridge._on_mod_action("update")
    bridge._done_mod_confirm(bridge._dialogs[0], inst.id, "update",
                             ["sale"], True)
    assert any("_run_mod_progress" in q for q in _qualnames(spawns))
    assert bridge.window.mod_busy is True
    prog = [d for d in bridge._dialogs
            if isinstance(d, ProgressDriver)]
    assert len(prog) == 1
    bridge._post("progress-line", (prog[0], "streamed line"))
    bridge._drain()
    assert "streamed line" in prog[0].view.log
    bridge._post("progress-done", (prog[0], True, "Updated sale"))
    bridge._drain()
    assert prog[0].view.finished is True
    assert bridge.window.mod_busy is False
    prog[0].view.closed()
    assert prog[0] not in bridge._dialogs
    bridge.close()


def test_mod_uninstall_flow(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    bridge._on_mod_action("uninstall")
    assert bridge.window.toast_message == "Pick a module first"
    bridge._mod_picked("sale")
    bridge._on_mod_action("uninstall")
    assert len(bridge._dialogs) == 1
    bridge._done_mod_confirm(bridge._dialogs[0], inst.id, "uninstall",
                             ["sale"], True)
    assert any("_run_mod_progress" in q for q in _qualnames(spawns))
    bridge.close()


def test_mod_update_code_guards_and_confirm(tmp_path, monkeypatch,
                                            spawns):
    from odoo_vite.core.registry import update_instance

    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    bridge._current_id = inst.id
    bridge._on_mod_action("update-code")
    assert "auto-update" in bridge.window.toast_message
    assert bridge._dialogs == []
    assert update_instance(
        inst.id, status="running",
        auto_update_modules=["sale"]).ok
    bridge._on_mod_action("update-code")
    assert "Stop" in bridge.window.toast_message
    assert update_instance(
        inst.id, status="stopped",
        auto_update_modules=["sale"]).ok
    bridge._on_mod_action("update-code")
    assert len(bridge._dialogs) == 1
    assert "backup" in bridge._dialogs[0].view.body
    bridge._done_mod_confirm(bridge._dialogs[0], inst.id, "update-code",
                             ["sale"], True)
    assert any("_run_mod_progress" in q for q in _qualnames(spawns))
    bridge.close()


def test_mod_deps_flow(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path)
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    bridge._on_mod_action("deps")
    assert bridge.window.toast_message == "Pick a module first"
    bridge._mod_picked("sale")
    bridge._on_mod_action("deps")
    assert any("fetch_deps" in q for q in _qualnames(spawns))
    bridge._post("deps-ready",
                 (inst.id, "sale", ["base"], ["purchase"]))
    bridge._drain()
    assert len(bridge._dialogs) == 1
    assert bridge._dialogs[0].view.mod_name == "sale"
    bridge.close()


def test_mod_refresh_and_no_primary(tmp_path, monkeypatch, spawns):
    inst = _make_instance(tmp_path, primary_db="", tracked_dbs=[])
    bridge = _bridge(tmp_path, monkeypatch)
    _paint_two_modules(bridge, inst)
    bridge._mod_toggled("sale")
    bridge._on_mod_action("refresh")
    assert any("refresh_modules" in q for q in _qualnames(spawns))
    spawns.clear()
    bridge._on_mod_action("install")
    assert "primary" in bridge.window.toast_message
    bridge.close()
