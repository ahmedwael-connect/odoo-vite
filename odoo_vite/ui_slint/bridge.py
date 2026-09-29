"""Slint bridge (PSS-3): AppWindow driver — sidebar, Overview, lifecycle.

Threading (queue-drain pattern): Slint owns the main thread. Callbacks
spawn coroutines onto a private asyncio loop thread; LifecycleOps runs
core off-thread there and posts ("message")/("refresh") payloads into a
queue. A 150ms Slint Timer drains the queue back on the UI thread — the
only place the component is touched. No QThread/forwarder/tracker port.
"""

from __future__ import annotations

import asyncio
import functools
import gc
import queue
import threading
import time
import traceback
import weakref
from datetime import timedelta
from pathlib import Path

import slint

# Import-hook parity with Qt (PSQ-4 rule): every module a loop-thread
# worker may touch must be imported HERE on the main thread. First-imports
# inside workers abort under shiboken's finder when GC coincides
# (verified: teardown abort in the Slint suite with pytest-qt loaded).
from odoo_vite.core import (
    addon_paths,
    adopt,
    audit,
    backup_scheduler,
    clone,
    conf_manager,
    conf_writer,
    db_backup,
    db_manager,
    db_state,
    devtools_export,
    enterprise as enterprise_core,
    git_manager,
    instance as instance_mod,
    log_doctor,
    log_search,
    module_manager,
    odoo_inspect,
    odoo_rpc,
    odoo_shell,
    proc,
    process_manager,
    profiler,
    provisioning,
    registry,
    removal,
    system_check,
    transfer as transfer_core,
    venv_manager,
)
# Binding counts as use (ruff F401): the point is importing, not naming.
_PREIMPORTED_FOR_WORKERS = (
    addon_paths, adopt, audit, backup_scheduler, clone, conf_manager,
    conf_writer, db_backup, db_manager, db_state, devtools_export,
    enterprise_core, git_manager, instance_mod, log_doctor, log_search,
    module_manager, odoo_inspect, odoo_rpc, odoo_shell, proc,
    process_manager, profiler, provisioning, registry, removal,
    system_check, transfer_core, venv_manager,
)
# Binding counts as use (ruff F401): the point is importing, not naming.
_PREIMPORTED_FOR_WORKERS = (
    addon_paths, audit, backup_scheduler, clone, conf_manager, conf_writer,
    db_backup, db_manager, db_state, devtools_export, enterprise_core,
    git_manager, instance_mod, log_doctor, log_search, module_manager,
    odoo_inspect, odoo_rpc, odoo_shell, proc, process_manager, profiler,
    provisioning, registry, removal, system_check, venv_manager,
)
from odoo_vite.core.log_tail import LogFollower, read_last_n
from odoo_vite.core.odoo_shell import OdooShell
from odoo_vite.core.process_manager import get_statuses
from odoo_vite.core.registry import get_instance
from odoo_vite.ui_slint.databases import DatabaseOps, pick_open_file
from odoo_vite.ui_slint.databases import pick_save_file as _pick_save
from odoo_vite.ui_slint.dialogs import (
    AboutDriver,
    AddonsDriver,
    AdoptDriver,
    CloneDialogDriver,
    ConfirmDriver,
    CreateDriver,
    DepsDriver,
    DiscoverDriver,
    FilesDriver,
    ImportDriver,
    PreferencesDriver,
    ProgressDriver,
    RecordDriver,
    ScaffoldDriver,
    ScheduleDriver,
    TypedConfirmDriver,
)
from odoo_vite.ui_slint.lifecycle import LifecycleOps
from odoo_vite.ui_slint.transfer import (
    TransferOps,
    bundle_filename,
    read_preferences,
    save_preferences,
)
from odoo_vite.ui_slint.wizards import WizardOps
from odoo_vite.ui_slint.logs import (
    LINE_CAP,
    LOG_LEVELS,
    LOG_TAIL_CAP,
    PROFILE_DURATIONS,
    LogOps,
    format_doctor_lines,
    format_search_lines,
    format_slow_lines,
    open_svg_external,
    parse_profile_duration,
)
from odoo_vite.ui_slint.configuration import (
    COMMON_KEYS,
    LOG_LEVELS as CONF_LOG_LEVELS,
    ConfigOps,
    pick_open_dir,
    read_conf_view,
    validate_meta,
)
from odoo_vite.ui_slint.devtools import (
    OPERATORS,
    DevToolsOps,
    diff_record_values,
    editable_fields,
    format_cron_line,
    format_meta_line,
    format_record_label,
)
from odoo_vite.ui_slint.modules import (
    STATE_FILTERS,
    ModuleOps,
    preview_command,
    state_category,
)
from odoo_vite.ui_slint.selection import (
    SelectionState,
    sync_model,
    sync_strings,
)

try:
    import keyring as _keyring  # noqa: F401  (pre-import for workers)
except ImportError:  # core degrades gracefully without it
    pass

POLL_MS = 2000
DRAIN_MS = 150
TAIL_MS = 1000
SHELL_MS = 500
TOAST_MS = 5000
LOGS_TAB = 4
DEV_TAB = 5


def _wcb(ref, name, *args):
    """Weak Slint callback: Slint-held callables must never strongly own
    the bridge, or component↔bridge refcount cycles form and cyclic GC may
    free Rust values on worker threads (Slint-Python Send abort, verified).
    With weak refs, teardown is plain refcounting on the dropping thread
    (main, by construction) — no GC involvement, ever."""
    owner = ref()
    if owner is not None:
        getattr(owner, name)(*args)


class SlintBridge:
    """Owns the component; only Timer callbacks and _drain() touch it."""

    def __init__(self) -> None:
        slint_path = Path(__file__).resolve().parent / "app.slint"
        module = slint.load_file(str(slint_path))
        self.window = module.AppWindow()
        self.sidebar = SelectionState(multi=False)
        self._current_id: str | None = None
        self._ent_key = None
        self._ent_text = ""
        self._ent_warn = False
        self._queue: queue.Queue = queue.Queue()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()
        # In-flight loop work (Qt wait_for_background parity): close() and
        # tests drain these so no worker outlives its bridge — stray
        # callbacks into later tests/GC abort the process (verified).
        self._futures: set = set()
        # Owned dialogs: Slint views holding models must be dismissed +
        # released on terminal paths (never left to cyclic GC on workers).
        self._dialogs: list = []
        self._ops = LifecycleOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            functools.partial(_wcb, weakref.ref(self), "_post_refresh"))
        self._dbops = DatabaseOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            functools.partial(_wcb, weakref.ref(self), "_post_refresh"),
            lambda iid, states: self._post("db-states", (iid, states)),
            lambda iid, report: self._post("db-report", (iid, report)),
            lambda iid, scheds, status: self._post(
                "db-schedules", (iid, scheds, status)),
            lambda iid, payload: self._post(
                "discover-ready", (iid, payload)))
        # In-flight DB work (UXS-2 busy gating): _track owns lifecycle;
        # this set only counts — the drain clears db-busy when it empties.
        self._db_inflight: set = set()
        self._modops = ModuleOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            functools.partial(_wcb, weakref.ref(self), "_post_refresh"),
            lambda iid, mods, diff, err: self._post(
                "modules-ready", (iid, mods, diff, err)),
            lambda iid, name, dep, req: self._post(
                "deps-ready", (iid, name, dep, req)))
        self._confops = ConfigOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            functools.partial(_wcb, weakref.ref(self), "_post_refresh"))
        self._conf_options: dict = {}
        self._transferops = TransferOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            functools.partial(_wcb, weakref.ref(self), "_post_refresh"))
        self._logops = LogOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            lambda iid, ok, msg, matches: self._post(
                "log-search", (iid, ok, msg, matches)),
            lambda iid, findings: self._post(
                "log-doctor", (iid, findings)),
            lambda iid, ok, msg, rows: self._post(
                "log-slow", (iid, ok, msg, rows)),
            lambda iid, ok, msg, svg: self._post(
                "profile-ready", (iid, ok, msg, svg)))
        self._follower = None
        self._tail_lines: list = []
        self._devops = DevToolsOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            lambda iid, text: self._post("dev-rpc", (iid, text)),
            lambda iid, models: self._post("dev-models", (iid, models)),
            lambda iid, meta: self._post("dev-meta", (iid, meta)),
            lambda iid, recs, off, more: self._post(
                "dev-records", (iid, recs, off, more)),
            lambda iid, crons: self._post("dev-crons", (iid, crons)))
        self._dev_models_state = SelectionState(multi=False)
        self._dev_recs_state = SelectionState(multi=False)
        self._dev_model = ""
        self._dev_meta: dict = {}
        self._dev_records: list = []
        self._dev_rec_current: str | None = None
        self._shells: dict = {}
        self._shell_lines: dict = {}
        self._shell_starting: set = set()
        self._mod_state = SelectionState(multi=True)
        self._mod_cache: list = []
        self._mod_diff: dict = {}
        self._mod_error = ""
        self._mod_needle = ""
        self._mod_filter_name = STATE_FILTERS[0]
        self._mod_current: str | None = None
        # Picks survive filtering: the checked set lives here, not in the
        # (rebuilt) rows — re-applied on every render (Qt _checked parity;
        # SelectionState.set_items treats input as the full set, so a
        # filter pass through it would otherwise drop picks).
        self._mod_checked: set = set()
        self._sched_ids: list[str] = []
        self._sched_objs: dict = {}
        self._last_states: dict = {}
        self._server_at = 0.0
        self._server_ok = True
        # Persistent models (assigned ONCE): refreshes sync in place via
        # selection.sync_model. Replacing models wholesale drops
        # struct-holding values on GC, which races Slint-Python's thread
        # checks when loop workers exist (verified abort in suite).
        self._sidebar_model = slint.ListModel([])
        self.window.sidebar_rows = self._sidebar_model
        self._table_model = slint.ListModel([])
        self.window.db_table = self._table_model
        self._table_cells: dict[str, object] = {}
        self._sched_model = slint.ListModel([])
        self.window.sched_rows = self._sched_model
        self._combo_model = slint.ListModel([])
        self.window.db_names = self._combo_model
        self._mod_model = slint.ListModel([])
        self.window.mod_rows = self._mod_model
        self._mod_filters = slint.ListModel(STATE_FILTERS)
        self.window.mod_filters = self._mod_filters
        self._conf_lines_model = slint.ListModel([])
        self.window.conf_lines = self._conf_lines_model
        self._conf_levels_model = slint.ListModel(CONF_LOG_LEVELS)
        self.window.conf_log_levels = self._conf_levels_model
        self._tail_model = slint.ListModel([])
        self.window.log_tail_lines = self._tail_model
        self._search_model = slint.ListModel([])
        self.window.log_search_lines = self._search_model
        self._doctor_model = slint.ListModel([])
        self.window.log_doctor_lines = self._doctor_model
        self._slow_model = slint.ListModel([])
        self.window.log_slow_lines = self._slow_model
        self._log_levels_model = slint.ListModel(LOG_LEVELS)
        self.window.log_levels = self._log_levels_model
        self._profile_durs_model = slint.ListModel(PROFILE_DURATIONS)
        self.window.log_profile_durs = self._profile_durs_model
        self._dev_model_rows = slint.ListModel([])
        self.window.dev_model_rows = self._dev_model_rows
        self._dev_rec_rows = slint.ListModel([])
        self.window.dev_rec_rows = self._dev_rec_rows
        self._dev_cron_model = slint.ListModel([])
        self.window.dev_cron_lines = self._dev_cron_model
        self._dev_shell_model = slint.ListModel([])
        self.window.dev_shell_lines = self._dev_shell_model
        self._dev_dom_ops = slint.ListModel(OPERATORS)
        self.window.dev_dom_ops = self._dev_dom_ops
        self._hide_timer = slint.Timer()
        self._hide_timeout = timedelta(milliseconds=TOAST_MS)
        me = weakref.ref(self)
        self.window.request_refresh = functools.partial(_wcb, me, "refresh")
        self.window.sidebar_picked = functools.partial(_wcb, me, "select")
        self.window.sidebar_filter_changed = functools.partial(
            _wcb, me, "_sidebar_search")
        self.window.sys_action = functools.partial(
            _wcb, me, "_on_sys_action")
        self.window.wiz_action = functools.partial(
            _wcb, me, "_on_wiz_action")
        self.window.db_action = functools.partial(_wcb, me, "_on_db_action")
        self.window.sched_action = functools.partial(
            _wcb, me, "_on_sched_action")
        self.window.sched_picked = functools.partial(
            _wcb, me, "_sched_picked")
        self.window.mod_action = functools.partial(
            _wcb, me, "_on_mod_action")
        self.window.mod_search_changed = functools.partial(
            _wcb, me, "_mod_search")
        self.window.mod_filter_changed = functools.partial(
            _wcb, me, "_mod_filter")
        self.window.mod_toggled = functools.partial(
            _wcb, me, "_mod_toggled")
        self.window.mod_picked = functools.partial(
            _wcb, me, "_mod_picked")
        self.window.conf_action = functools.partial(
            _wcb, me, "_on_conf_action")
        self.window.log_action = functools.partial(
            _wcb, me, "_on_log_action")
        self.window.dev_action = functools.partial(
            _wcb, me, "_on_dev_action")
        self.window.dev_models_picked = functools.partial(
            _wcb, me, "_dev_models_picked")
        self.window.dev_rec_picked = functools.partial(
            _wcb, me, "_dev_rec_picked")
        self.window.ov_start = functools.partial(_wcb, me, "_ui_start")
        self.window.ov_stop = functools.partial(_wcb, me, "_ui_stop")
        self.window.ov_restart = functools.partial(_wcb, me, "_ui_restart")
        self.window.ov_remove = functools.partial(_wcb, me, "_remove_flow")
        self.window.ov_clone = functools.partial(_wcb, me, "_clone_flow")
        self.window.ov_export = functools.partial(
            _wcb, me, "_export_flow")
        self._poll = slint.Timer()
        self._poll.start(
            slint.TimerMode.Repeated, timedelta(milliseconds=POLL_MS),
            functools.partial(_wcb, me, "refresh"))
        self._drain_timer = slint.Timer()
        self._drain_timer.start(
            slint.TimerMode.Repeated, timedelta(milliseconds=DRAIN_MS),
            functools.partial(_wcb, me, "_drain"))
        self._tail_timer = slint.Timer()
        self._tail_timer.start(
            slint.TimerMode.Repeated, timedelta(milliseconds=TAIL_MS),
            functools.partial(_wcb, me, "_tail_tick"))
        self._shell_timer = slint.Timer()
        self._shell_timer.start(
            slint.TimerMode.Repeated, timedelta(milliseconds=SHELL_MS),
            functools.partial(_wcb, me, "_shell_poll_tick"))
        self.refresh()

    def _ui_start(self) -> None:
        self._start_flow()

    def _ui_stop(self) -> None:
        self._op("stop", self._current_id)

    def _ui_restart(self) -> None:
        self._op("restart", self._current_id)

    def _hide_toast(self) -> None:
        self.window.toast_showing = False

    # ------------------------------------------------------------ UI thread

    def refresh(self) -> None:
        try:
            statuses = get_statuses()
        except Exception:
            statuses = []
        self.sidebar.set_items([
            {"id": s.get("id", ""), "title": s.get("name", "?"),
             "badge": str(s.get("status", "")).capitalize()}
            for s in statuses])
        sync_model(self._sidebar_model, self.sidebar.view_rows())
        self.window.sidebar_counts = self.sidebar.counts_text()
        self.window.sidebar_empty = (
            "" if statuses
            else "No instances yet — create or adopt one to begin.")
        if self._current_id is not None:
            inst = get_instance(self._current_id)
            if inst is not None:
                self.window.app_title = f"Odoo Vite — {inst.name}"
                self._paint_overview(inst)
                self._paint_db_names(inst)
                self._paint_conf(inst)
            else:
                self.window.app_title = "Odoo Vite (Slint)"
        self.window.status_line = f"{len(statuses)} instance(s) — Ready"
        self._maybe_probe_server()

    def select(self, instance_id: str) -> None:
        instance_id = str(instance_id)
        self._current_id = instance_id
        self.sidebar.move_current(instance_id)
        self.window.sidebar_selected = instance_id
        inst = get_instance(instance_id)
        if inst is not None:
            self.window.app_title = f"Odoo Vite — {inst.name}"
        if inst is not None:
            self._ent_key = None  # force enterprise re-eval once
            self._paint_overview(inst)
            self._paint_db_names(inst)
            self.window.db_has_instance = True
            self._spawn_db("refresh_states", instance_id)
            self._spawn_db("refresh_schedules", instance_id)
            self.window.mod_has_instance = True
            self.window.conf_has_instance = True
            self._paint_conf(inst)
            self.window.log_has_instance = True
            self._start_tail(inst)
            self.window.dev_has_instance = True
            self._reset_dev_view()
            self.window.mod_db = (f"on {inst.primary_db}"
                                  if inst.primary_db
                                  else "no primary database")
            self._mod_cache = []
            self._mod_diff = {}
            self._mod_error = ""
            self._mod_state = SelectionState(multi=True)
            self._mod_current = None
            self._mod_checked = set()
            self.window.mod_selected = ""
            self.window.mod_search = ""
            self._mod_needle = ""
            self._mod_error = "Loading…"
            self._render_mod_rows()
            self._spawn_mod("refresh_modules", instance_id)

    def show_toast(self, message: str, kind: str = "info") -> None:
        self.window.toast_message = message
        self.window.toast_kind = kind
        self.window.toast_showing = True
        self._hide_timer.start(
            slint.TimerMode.SingleShot, self._hide_timeout,
            functools.partial(
                _wcb, weakref.ref(self), "_hide_toast"))

    def _sidebar_search(self, text: str) -> None:
        self.sidebar.set_filter(text or "")
        sync_model(self._sidebar_model, self.sidebar.view_rows())
        self.window.sidebar_counts = self.sidebar.counts_text()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "message":
                    self.show_toast(*payload)
                elif kind == "refresh":
                    self.refresh()
                elif kind == "confirm-start":
                    self._confirm_start(*payload)
                elif kind == "db-states":
                    self._paint_states(*payload)
                elif kind == "db-report":
                    self._paint_report(*payload)
                elif kind == "db-schedules":
                    self._paint_schedules(*payload)
                elif kind == "server":
                    self._paint_server(payload)
                elif kind == "discover-ready":
                    self._open_discover(*payload)
                elif kind == "files-ready":
                    self._open_files(*payload)
                elif kind == "backup-pick":
                    self._continue_backup(*payload)
                elif kind == "restore-pick":
                    self._continue_restore(*payload)
                elif kind == "export-pick":
                    self._continue_export(*payload)
                elif kind == "import-pick":
                    self._continue_import(payload)
                elif kind == "confirm-switch":
                    self._confirm_switch(*payload)
                elif kind == "modules-ready":
                    self._paint_modules(*payload)
                elif kind == "deps-ready":
                    self._paint_deps(*payload)
                elif kind == "db-idle":
                    if not self._db_inflight:
                        self.window.db_busy = False
                elif kind == "addons-pick":
                    self._continue_addons_browse(*payload)
                elif kind == "python-pick":
                    if payload:
                        self.window.conf_python = payload
                elif kind == "log-search":
                    self._paint_search(*payload)
                elif kind == "log-doctor":
                    self._paint_doctor(*payload)
                elif kind == "log-slow":
                    self._paint_slow(*payload)
                elif kind == "profile-ready":
                    self._paint_profile(*payload)
                elif kind == "dev-rpc":
                    self._paint_rpc(*payload)
                elif kind == "dev-models":
                    self._paint_models(*payload)
                elif kind == "dev-meta":
                    self._paint_meta(*payload)
                elif kind == "dev-records":
                    self._paint_records(*payload)
                elif kind == "dev-crons":
                    self._paint_crons(*payload)
                elif kind == "dev-progress-done":
                    self._paint_dev_progress_done(*payload)
                elif kind == "shell-started":
                    self._paint_shell_started(*payload)
                elif kind == "shell-start-failed":
                    self._paint_shell_start_failed(*payload)
                elif kind == "shell-stopped":
                    self._paint_shell_stopped(payload)
                elif kind == "wiz-branches":
                    self._paint_wiz_branches(*payload)
                elif kind == "wiz-syscheck":
                    self._paint_wiz_syscheck(*payload)
                elif kind == "wiz-log":
                    self._paint_wiz_log(*payload)
                elif kind == "wiz-done":
                    self._paint_wiz_done(*payload)
                elif kind == "wiz-discarded":
                    self._paint_wiz_discarded(*payload)
                elif kind == "adopt-browse-conf":
                    self._continue_adopt_browse("conf", *payload)
                elif kind == "adopt-browse-community":
                    self._continue_adopt_browse("community", *payload)
                elif kind == "scaffold-dest-pick":
                    driver, path = payload
                    if self._wiz_owned(driver) and path:
                        driver.set_dest(path)
                elif kind == "wiz-adopt-done":
                    self._paint_wiz_adopt_done(*payload)
                elif kind == "progress-line":
                    self._paint_progress_line(*payload)
                elif kind == "progress-done":
                    self._paint_progress_done(*payload)
        except queue.Empty:
            pass

    def run(self) -> None:
        self.window.run()

    def close(self) -> None:
        """Teardown mirror of __init__ (Qt closeEvent-drain parity).

        Stops Slint timers, releases owned dialogs, drains loop work, then
        stops the loop and its executor. The app calls this on quit; tests
        call it per test (see fixtures in tests/test_slint_bridge.py).
        """
        for session in list(self._shells.values()):
            try:
                session.stop()
            except Exception:
                pass
        self._shells.clear()
        self.wait_idle(timeout=15.0)
        for drv in list(self._dialogs):
            self._release(drv)
        for timer in (self._poll, self._drain_timer, self._hide_timer,
                      self._tail_timer, self._shell_timer):
            try:
                timer.stop()
            except Exception:
                pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5.0)
        except Exception:
            pass
        # Executor threads are non-daemon and outlive loop.stop(): shut
        # them down so one bridge never leaks threads into the next test
        # (or, in prod, across quit/reopen cycles).
        try:
            ex = getattr(self._loop, "_default_executor", None)
            if ex is not None:
                ex.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass
        # Final quiescent collect: loops and executors are gone, so the
        # owner thread frees whatever cyclic trash remains (PSS-9).
        gc.collect()

    def wait_idle(self, timeout: float = 15.0) -> bool:
        """Block until spawned loop work finishes (bounded). Returns True
        when quiet — tests assert this instead of sleeping."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            live = [f for f in list(self._futures) if not f.done()]
            if not live:
                return True
            time.sleep(0.05)
        return False

    def _track(self, fut):
        self._futures.add(fut)

        def _done(f) -> None:
            _log_failure(f)
            self._futures.discard(f)

        fut.add_done_callback(_done)
        return fut

    # ------------------------------------------------------- cross-thread in

    def _post_message(self, text: str, kind: str = "info") -> None:
        self._queue.put(("message", (text, kind)))

    def _post_refresh(self) -> None:
        self._queue.put(("refresh", None))

    def _post(self, kind: str, payload) -> None:
        self._queue.put((kind, payload))

    def _spawn_db(self, op: str, *args) -> None:
        """Schedule a DatabaseOps coroutine (results via typed sinks).

        Marks the Databases tab busy until every in-flight op lands —
        buttons bind `enabled` to it, so a 10s backup can't double-fire.
        """
        coro = getattr(self._dbops, op)(*args)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self._track(fut)
        self._db_inflight.add(fut)
        self.window.db_busy = True

        def _db_done(f) -> None:
            _log_failure(f)
            self._db_inflight.discard(f)
            self._post("db-idle", None)

        fut.add_done_callback(_db_done)

    def _spawn_mod(self, op: str, *args) -> None:
        """Schedule a ModuleOps coroutine (results via typed sinks)."""
        coro = getattr(self._modops, op)(*args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _db_instance(self):
        if self._current_id is None:
            self.show_toast("Select an instance first", "error")
            return None
        inst = get_instance(self._current_id)
        if inst is None:
            self.show_toast("Instance disappeared", "error")
            return None
        return inst

    def _op(self, op: str, instance_id: str | None, *args) -> None:
        """Schedule a LifecycleOps coroutine from a Slint callback.

        run_coroutine_threadsafe is thread-safe from any thread; results
        come back through the queue drained on the UI thread.
        """
        if instance_id is None:
            self.show_toast("Select an instance first", "error")
            return
        coro = getattr(self._ops, op)(instance_id, *args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _own(self, driver):
        """Retain a dialog driver for its session (explicit lifetime).

        Slint views holding models must be dismissed + released on the UI
        thread — never left for cyclic GC on workers (Send abort). The
        terminal _done_* path always releases; Cancel releases too.
        """
        self._dialogs.append(driver)
        try:
            driver.view.cancelled = functools.partial(
                _wcb, weakref.ref(self), "_release", driver)
        except Exception:
            pass
        return driver

    def _release(self, driver) -> None:
        try:
            driver.dismiss()
        except Exception:
            pass
        try:
            self._dialogs.remove(driver)
        except ValueError:
            pass
        # Quiescent collect on the owner thread (PSS-9 production rule):
        # automatic GC is off, so cyclic dialog trash (lambdas closing
        # over their driver) is freed here, never on a worker thread.
        gc.collect()

    def _start_flow(self) -> None:
        """Start, surfacing first-create confirms as a dialog (Qt parity).

        Core returns needs_confirm instead of blocking; the confirm answer
        re-runs start with an already-confirmed callback — all async.
        """
        iid = self._current_id
        if iid is None:
            self.show_toast("Select an instance first", "error")
            return
        self._track(asyncio.run_coroutine_threadsafe(
            _run_start(self._ops, self._queue, iid), self._loop))

    def _confirm_start(self, instance_id: str, preview: dict) -> None:
        detail = (preview or {}).get("detail", "")
        drv = ConfirmDriver(
            "Create database?",
            f"{detail}\n\nRuns odoo-bin -i base, then starts.".strip(),
            "Create + Start",
            on_result=lambda ok: self._done_confirm_start(
                drv, instance_id, ok))
        self._own(drv).show()

    def _done_confirm_start(self, drv, instance_id: str, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._op("start", instance_id, None, lambda _p: True)

    # ---------------------------------------------------------------- flows

    def _remove_flow(self) -> None:
        inst = self._current()
        if inst is None:
            return
        if (inst.mode or "managed") == "adopted":
            drv = ConfirmDriver(
                f"Remove '{inst.name}'?", "Files and DB are NOT touched.",
                "Remove", destructive=True,
                on_result=lambda ok: self._done_remove(drv, inst.id, ok))
        else:
            drv = TypedConfirmDriver(
                f"Remove '{inst.name}'?",
                f"Type '{inst.name}' to confirm. Databases are kept "
                "(drop them from Databases first).",
                inst.name, "Remove",
                on_result=lambda ok: self._done_remove(drv, inst.id, ok))
        self._own(drv).show()

    def _done_remove(self, drv, instance_id: str, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._op("remove", instance_id)

    def _clone_flow(self) -> None:
        inst = self._current()
        if inst is None:
            return
        if (inst.status or "") == "running":
            self.show_toast(f"Stop '{inst.name}' before cloning it",
                              "error")
            return
        try:
            port = provisioning.suggest_port((inst.port or 8069) + 1)
        except Exception:
            port = (inst.port or 8069) + 1
        drv = CloneDialogDriver(
            inst.name, f"{inst.name} (clone)", port,
            on_confirm=lambda name, p: self._done_clone(
                drv, inst.id, name, p))
        self._own(drv).show()

    def _done_clone(self, drv, instance_id: str, name: str,
                    port: int) -> None:
        self._release(drv)
        self._op("clone", instance_id, name, port)

    def _current(self):
        if self._current_id is None:
            return None
        return get_instance(self._current_id)

    # ------------------------------------------------------------ databases

    def _on_db_action(self, action: str) -> None:
        """Dispatch grouped DatabasesView actions (Qt _on_db_action parity)."""
        inst = self._db_instance()
        if inst is None:
            return
        picked = (self.window.db_picked or "").strip()
        if action == "set-primary":
            if not picked:
                self.show_toast("Pick a database first", "error")
                return
            self._spawn_db("set_primary", inst.id, picked)
        elif action == "switch":
            if not picked:
                self.show_toast("Pick a database first", "error")
                return
            self._switch_flow(inst, picked)
        elif action == "track":
            manual = (self.window.db_manual or "").strip()
            if not manual:
                self.show_toast("Type a database name first", "error")
                return
            self.window.db_manual = ""
            self._spawn_db("track_many", inst.id, [manual])
        elif action == "discover":
            self._spawn_db("discover_entries", inst.id)
        elif action == "refresh-states":
            self._spawn_db("refresh_states", inst.id)
        elif action == "init-db":
            if not picked:
                self.show_toast("Pick a database first", "error")
                return
            self._spawn_db("init_db", inst.id, picked)
        elif action == "drop-db":
            self._drop_flow(inst, picked)
        elif action == "backup-db":
            if not picked:
                self.show_toast("Pick a database first", "error")
                return
            self._spawn_picker("backup-pick", inst.id, picked)
        elif action == "restore-db":
            self._spawn_picker("restore-pick", inst.id, picked)
        elif action == "validate":
            self._spawn_db("validate", inst.id)

    def _on_sched_action(self, action: str) -> None:
        """Dispatch grouped schedule actions (Qt DatabasesPage parity)."""
        inst = self._db_instance()
        if inst is None:
            return
        names = [inst.primary_db] if inst.primary_db else []
        names += [d for d in (inst.tracked_dbs or []) if d not in names]
        if action == "add":
            if not names:
                self.show_toast("Track a database first", "error")
                return
            drv = ScheduleDriver(
                names,
                on_save=lambda payload: self._done_sched_save(
                    drv, inst.id, None, payload),
                on_message=self.show_toast)
            self._own(drv).show()
            return
        if action in ("edit", "run", "toggle", "delete"):
            sched = self._selected_schedule()
            if sched is None:
                self.show_toast("Pick a schedule first", "error")
                return
            if action == "edit":
                edit_drv = ScheduleDriver(
                    names, existing=sched,
                    on_save=lambda payload: self._done_sched_save(
                        edit_drv, inst.id, sched.id, payload),
                    on_message=self.show_toast)
                self._own(edit_drv).show()
            elif action == "run":
                self._spawn_db("run_schedule_now", sched.id)
            elif action == "toggle":
                self._spawn_db("sched_toggle", sched.id, not sched.enabled)
            else:
                del_drv = ConfirmDriver(
                    f"Delete schedule {sched.cron}?",
                    "Scheduled backups stop; existing dumps stay on disk.",
                    "Delete", destructive=True,
                    on_result=lambda ok: self._done_sched_delete(
                        del_drv, sched.id, ok))
                self._own(del_drv).show()
            return
        if action == "files":
            self._spawn_files(inst)

    def _done_sched_save(self, drv, instance_id: str,
                         schedule_id: str | None, payload: dict) -> None:
        self._release(drv)
        if schedule_id is None:
            self._spawn_db("sched_create", instance_id, payload)
        else:
            self._spawn_db("sched_update", schedule_id, payload)

    def _done_sched_delete(self, drv, schedule_id: str,
                           ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_db("sched_delete", schedule_id)

    def _selected_schedule(self):
        try:
            idx = int(self.window.sched_row)
        except (TypeError, ValueError):
            return None
        if idx < 0 or idx >= len(self._sched_ids):
            return None
        return self._sched_objs.get(self._sched_ids[idx])

    def _sched_picked(self, idx: int) -> None:
        """ListView rows don't track selection natively — the view reports
        the clicked index and Python owns it (headless-safe explicit set)."""
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return
        if 0 <= idx < len(self._sched_ids):
            self.window.sched_row = idx

    def _spawn_picker(self, kind: str, instance_id: str,
                      picked: str) -> None:
        """zenity blocks — run it on the loop thread, continue on drain."""
        self._track(asyncio.run_coroutine_threadsafe(
            _run_picker(self._queue, kind, instance_id, picked),
            self._loop))

    def _spawn_files(self, inst) -> None:
        self._track(asyncio.run_coroutine_threadsafe(
            _run_files(self._queue, inst.id, inst.name), self._loop))

    def _drop_flow(self, inst, picked: str) -> None:
        if not picked:
            self.show_toast("Pick a database first", "error")
            return
        if picked == (inst.primary_db or ""):
            self.show_toast("The primary database cannot be dropped here — "
                            "switch primary first, or remove the instance",
                            "error")
            return
        drv = TypedConfirmDriver(
            f"Drop database '{picked}'?",
            "This permanently deletes the database. The instance keeps "
            "running on its primary.",
            picked, "Drop permanently",
            on_result=lambda ok: self._done_drop(
                drv, inst.id, picked, ok))
        self._own(drv).show()

    def _done_drop(self, drv, instance_id: str, picked: str,
                   ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_db("drop_db", instance_id, picked)

    def _continue_backup(self, instance_id: str, db_name: str,
                         dest: str) -> None:
        if not dest:
            return
        self._spawn_db("backup_db", instance_id, db_name, dest)

    def _continue_restore(self, instance_id: str, picked: str,
                          dump: str) -> None:
        """PSS-4 scope: restore target is the picked database (Qt allowed
        an arbitrary target; noted in the migration plan)."""
        if not dump:
            return
        if not picked:
            self.show_toast("Pick a database first", "error")
            return
        drv = TypedConfirmDriver(
            "Restore database?",
            f"Retype the target name to restore into '{picked}'. "
            "The target is DROPPED and recreated — never merged.",
            picked, "Restore (drop + recreate)",
            on_result=lambda ok: self._done_restore(
                drv, instance_id, dump, picked, ok))
        self._own(drv).show()

    def _done_restore(self, drv, instance_id: str, dump: str,
                      picked: str, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_db("restore_db", instance_id, dump, picked)

    def _open_discover(self, instance_id: str, payload: dict) -> None:
        if not payload.get("ok"):
            self.show_toast(payload.get("message", "Discover failed"),
                             "error")
            return
        inst = get_instance(instance_id)
        if inst is None:
            return
        drv = DiscoverDriver(
            payload.get("entries", []), inst.version or "",
            on_track=lambda names: self._done_discover_track(
                drv, instance_id, names),
            on_message=self.show_toast)
        self._own(drv).show()

    def _done_discover_track(self, drv, instance_id: str,
                             names: list) -> None:
        self._release(drv)
        self._spawn_db("track_many", instance_id, names)

    def _open_files(self, instance_id: str, files: list) -> None:
        if not files:
            self.show_toast("No backup files yet — run a backup first")
            return
        drv = FilesDriver(
            files,
            on_restore=lambda path: self._continue_restore(
                instance_id, self.window.db_picked or "", path),
            on_delete=lambda path: self._done_files_delete_ask(
                drv, instance_id, path),
            on_message=self.show_toast)
        self._own(drv).show()

    def _done_files_delete_ask(self, drv, instance_id: str,
                               path: str) -> None:
        confirm = ConfirmDriver(
            f"Delete '{path}'?",
            "The dump file is removed from disk.",
            "Delete", destructive=True,
            on_result=lambda ok: self._done_files_delete(
                drv, confirm, path, ok))
        self._own(confirm).show()

    def _done_files_delete(self, drv, confirm, path: str,
                           ok: bool) -> None:
        self._release(confirm)
        if ok:
            self._release(drv)
            self._spawn_db("file_delete", path)

    def _switch_flow(self, inst, picked: str) -> None:
        self._track(asyncio.run_coroutine_threadsafe(
            _run_switch(self._dbops, self._queue, inst.id, picked),
            self._loop))

    def _confirm_switch(self, instance_id: str, picked: str,
                        preview: dict) -> None:
        detail = (preview or {}).get("detail", "")
        drv = ConfirmDriver(
            f"Switch to '{picked}'?",
            f"{detail}\n\nThe database will be created first.".strip(),
            "Switch",
            on_result=lambda ok: self._done_switch_confirm(
                drv, instance_id, picked, ok))
        self._own(drv).show()

    def _done_switch_confirm(self, drv, instance_id: str, picked: str,
                             ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_db("switch_db", instance_id, picked,
                           lambda _p: True)

    # --------------------------------------------------------------- modules

    def _mod_instance(self):
        if self._current_id is None:
            self.show_toast("Select an instance first", "error")
            return None
        inst = get_instance(self._current_id)
        if inst is None:
            self.show_toast("Instance disappeared", "error")
            return None
        return inst

    def _on_mod_action(self, action: str) -> None:
        """Dispatch grouped ModulesView actions (Qt ModulesPage parity)."""
        inst = self._mod_instance()
        if inst is None:
            return
        if action == "refresh":
            self._spawn_mod("refresh_modules", inst.id)
        elif action in ("install", "update"):
            self._mod_install_update_flow(inst, action)
        elif action == "uninstall":
            self._mod_uninstall_flow(inst)
        elif action == "update-code":
            self._update_code_flow(inst)
        elif action == "deps":
            name = self._mod_current
            if not name:
                self.show_toast("Pick a module first", "error")
                return
            self._spawn_mod("fetch_deps", inst.id, name)
        elif action == "scaffold":
            self._open_scaffold_wizard()

    def _mod_checked_or_toast(self, verb: str):
        names = self._mod_state.checked_ids()
        if not names:
            self.show_toast(f"Select modules to {verb} first", "error")
            return None
        return names

    def _mod_install_update_flow(self, inst, action: str) -> None:
        verb = "Install" if action == "install" else "Update"
        names = self._mod_checked_or_toast(verb.lower())
        if not names:
            return
        db_name = (inst.primary_db or "").strip()
        if not db_name:
            self.show_toast("No primary database set — pick one first.",
                              "error")
            return
        flag = "-i" if action == "install" else "-u"
        cmd = preview_command(inst, db_name, flag, names)
        drv = ConfirmDriver(
            f"{verb} {', '.join(names)} into '{db_name}'?",
            f"Runs:\n{' '.join(cmd)}", verb,
            on_result=lambda ok: self._done_mod_confirm(
                drv, inst.id, action, names, ok))
        self._own(drv).show()

    def _mod_uninstall_flow(self, inst) -> None:
        name = self._mod_current
        if not name:
            self.show_toast("Pick a module first", "error")
            return
        db_name = (inst.primary_db or "").strip()
        if not db_name:
            self.show_toast("No primary database set — pick one first.",
                              "error")
            return
        cmd = (f"{inst.venv_path}/bin/python "
               f"{inst.community_path}/odoo-bin -c {inst.conf_path} "
               f"-d {db_name} --uninstall {name} --stop-after-init")
        drv = ConfirmDriver(
            f"Uninstall {name} from '{db_name}'?",
            f"Runs:\n{cmd}", "Uninstall", destructive=True,
            on_result=lambda ok: self._done_mod_confirm(
                drv, inst.id, "uninstall", [name], ok))
        self._own(drv).show()

    def _done_mod_confirm(self, drv, instance_id: str, op: str,
                          names: list, ok: bool) -> None:
        self._release(drv)
        if not ok:
            return
        if op == "update-code":
            factory = lambda emit, cancel: self._modops.update_code(
                instance_id, progress_cb=emit, cancel=cancel)
        else:
            method = getattr(self._modops, op)
            factory = lambda emit, cancel: method(
                instance_id, names, progress_cb=emit, cancel=cancel)
        title = f"{op.replace('-', ' ').title()} {', '.join(names)}"
        prog = ProgressDriver(title)
        prog.on_close = lambda: self._release(prog)
        self._own(prog).show()
        self.window.mod_busy = True
        self._track(asyncio.run_coroutine_threadsafe(
            _run_mod_progress(self._queue, prog, factory), self._loop))

    def _update_code_flow(self, inst) -> None:
        """3-stage pipeline confirm (Qt risk copy parity).

        PSS-5a divergence: Qt offered an automatic pre-update backup;
        Slint recommends a manual backup (Databases tab) — no auto-backup
        flow yet.
        """
        if (inst.status or "") == "running":
            self.show_toast("Stop the instance first — code changes under "
                            "a live server would half-apply", "error")
            return
        mods = list(inst.auto_update_modules or [])
        if not mods:
            self.show_toast("No auto-update modules configured — nothing "
                            "to update", "error")
            return
        drv = ConfirmDriver(
            "Update code and modules?",
            "Riskier than Install: git pull community, pip install, then "
            f"-u {','.join(mods)} (instance stays stopped; restart it "
            "yourself afterwards). Back up the primary database first "
            "(Databases tab) — no automatic backup here.",
            "Update code", destructive=True,
            on_result=lambda ok: self._done_mod_confirm(
                drv, inst.id, "update-code", mods, ok))
        self._own(drv).show()

    def _mod_search(self, text: str) -> None:
        self._mod_needle = text or ""
        self._render_mod_rows()

    def _mod_filter(self, idx: int) -> None:
        try:
            self._mod_filter_name = STATE_FILTERS[int(idx)]
        except (IndexError, TypeError, ValueError):
            self._mod_filter_name = STATE_FILTERS[0]
        # Set explicitly: headless callers bypass the ComboBox two-way bind.
        self.window.mod_filter_idx = STATE_FILTERS.index(
            self._mod_filter_name)
        self._render_mod_rows()

    def _mod_toggled(self, item_id: str) -> None:
        item_id = str(item_id)
        known = any(r["id"] == item_id and not r.get("header")
                    for r in self._mod_state._rows)
        if not known:
            return
        if item_id in self._mod_checked:
            self._mod_checked.discard(item_id)
        else:
            self._mod_checked.add(item_id)
        self._render_mod_rows()

    def _mod_picked(self, item_id: str) -> None:
        self._mod_current = str(item_id)
        self.window.mod_selected = str(item_id)
        self._mod_state.move_current(item_id)

    def _render_mod_rows(self) -> None:
        """Filter the module cache (Qt _render_rows parity) and push."""
        needle = (self._mod_needle or "").strip().lower()
        rows = []
        for mod in self._mod_cache:
            name = mod.get("name", "")
            if needle and needle not in name.lower() and needle not in str(
                    mod.get("summary", "")).lower():
                continue
            if self._mod_filter_name != "All" and \
                    state_category(mod) != self._mod_filter_name:
                continue
            badge = state_category(mod)
            info = (self._mod_diff or {}).get(name)
            if info and info.get("status") not in (None, "in-sync"):
                badge += f"  ⚠ {info.get('note', '')}"
            rows.append({"id": name, "title": name, "badge": badge,
                         "checked": name in self._mod_checked})
        # Explicit flags win: picks live in _mod_checked, not in the rows.
        self._mod_state.set_items(rows)
        sync_model(self._mod_model, self._mod_state.view_rows())
        self.window.mod_counts = self._mod_state.counts_text()
        self.window.mod_has_checked = bool(
            self._mod_state.checked_ids())
        if rows:
            self.window.mod_empty = ""
        else:
            self.window.mod_empty = self._mod_error or (
                "No modules match — adjust the filter.")

    def _paint_modules(self, instance_id: str, modules: list, diff: dict,
                       error: str) -> None:
        if instance_id != self._current_id:
            return
        self._mod_cache = modules or []
        self._mod_diff = diff or {}
        self._mod_error = error or ""
        self._render_mod_rows()

    def _paint_deps(self, instance_id: str, name: str, depends: list,
                    required_by: list) -> None:
        if instance_id != self._current_id:
            return
        drv = DepsDriver(name, depends, required_by)
        self._own(drv).show()

    def _paint_progress_line(self, driver, line: str) -> None:
        if driver in self._dialogs and driver.view is not None:
            driver.append(line)

    def _paint_progress_done(self, driver, ok: bool,
                             message: str) -> None:
        self.window.mod_busy = False
        if driver in self._dialogs and driver.view is not None:
            driver.done(ok, message)

    # -------------------------------------------------------- configuration

    def _spawn_conf(self, op: str, *args) -> None:
        """Schedule a ConfigOps coroutine (message + refresh sinks)."""
        coro = getattr(self._confops, op)(*args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _paint_conf(self, inst) -> None:
        """Local instant repaint (Qt refresh_conf parity — no workers)."""
        view = read_conf_view(inst)
        self.window.conf_notice = ""
        self.window.conf_py_error = ""
        if view.get("error"):
            self.window.conf_path = view["error"]
            self._conf_options = {}
            sync_strings(self._conf_lines_model, [])
            self.window.conf_has_backup = False
            self.window.conf_backup_label = ""
            return
        self._conf_options = dict(view["options"])
        self.window.conf_path = view["conf_path"]
        sync_strings(self._conf_lines_model, view["lines"])
        common = view["common"]
        self.window.conf_db_host = common.get("db_host", "")
        self.window.conf_db_port = common.get("db_port", "")
        self.window.conf_db_user = common.get("db_user", "")
        self.window.conf_xmlrpc_port = common.get("xmlrpc_port", "")
        self.window.conf_logfile = common.get("logfile", "")
        self.window.conf_addons = view["addons_path"]
        if view["backup_path"]:
            self.window.conf_has_backup = True
            self.window.conf_backup_label = (
                f"Backup: {view['backup_path']}")
        else:
            self.window.conf_has_backup = False
            self.window.conf_backup_label = ""
        self.window.conf_description = view["description"]
        self.window.conf_workers = int(view["workers"] or 0)
        try:
            self.window.conf_log_idx = CONF_LOG_LEVELS.index(
                view["log_level"] or "info")
        except ValueError:
            self.window.conf_log_idx = 0
        self.window.conf_python = view["python_binary"]

    def _on_conf_action(self, action: str) -> None:
        """Dispatch grouped ConfigurationView actions (Qt page parity)."""
        if self._current_id is None:
            self.show_toast("Select an instance first", "error")
            return
        inst = get_instance(self._current_id)
        if inst is None:
            self.show_toast("Instance disappeared", "error")
            return
        if action == "conf-save":
            changes = {}
            current = {
                "db_host": self.window.conf_db_host,
                "db_port": self.window.conf_db_port,
                "db_user": self.window.conf_db_user,
                "xmlrpc_port": self.window.conf_xmlrpc_port,
                "logfile": self.window.conf_logfile,
            }
            for key in COMMON_KEYS:
                if current[key] != self._conf_options.get(key, ""):
                    changes[key] = current[key]
            if not changes:
                self.window.conf_notice = "No changes to save."
                return
            self.window.conf_notice = ""
            self._spawn_conf("save", inst.id, changes)
        elif action == "raw-set":
            key = (self.window.conf_raw_key or "").strip()
            if not key:
                self.window.conf_notice = "Enter a key name first."
                return
            value = self.window.conf_raw_value
            self.window.conf_raw_key = ""
            self.window.conf_raw_value = ""
            self.window.conf_notice = ""
            self._spawn_conf("save", inst.id,
                             {key: (None if value == "" else value)})
        elif action == "conf-restore":
            self._spawn_conf("restore", inst.id)
        elif action == "conf-regenerate":
            drv = ConfirmDriver(
                "Regenerate odoo.conf from registry?",
                "Rebuilds [options] from registry fields — manual edits "
                "to the file will be overwritten.",
                "Regenerate", destructive=True,
                on_result=lambda ok: self._done_conf_regen(
                    drv, inst.id, ok))
            self._own(drv).show()
        elif action == "addons-manage":
            try:
                entries = addon_paths.get_addons_state(inst)
            except Exception as exc:
                self.show_toast(f"Cannot load addon paths: {exc}", "error")
                return
            drv = AddonsDriver(
                entries,
                on_apply=lambda payload: self._done_addons_apply(
                    drv, inst.id, payload),
                on_message=self.show_toast,
                on_browse=lambda: self._spawn_addons_browse(drv))
            self._own(drv).show()
        elif action == "meta-save":
            try:
                workers = int(self.window.conf_workers)
            except (TypeError, ValueError):
                workers = 0
            try:
                log_level = CONF_LOG_LEVELS[int(self.window.conf_log_idx)]
            except (IndexError, TypeError, ValueError):
                log_level = "info"
            meta = {
                "description": self.window.conf_description,
                "workers": workers,
                "log_level": log_level,
                "python_binary": (self.window.conf_python or "").strip(),
            }
            err = validate_meta(meta)
            if err is not None:
                self.window.conf_py_error = err
                return
            self.window.conf_py_error = ""
            self._spawn_conf("meta_save", inst.id, meta)
        elif action == "browse-python":
            self._track(asyncio.run_coroutine_threadsafe(
                _run_conf_picker(self._queue, "python-pick"), self._loop))

    def _done_conf_regen(self, drv, instance_id: str, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_conf("regenerate", instance_id)

    def _done_addons_apply(self, drv, instance_id: str,
                           entries: list) -> None:
        self._release(drv)
        self._spawn_conf("apply_addons", instance_id, entries)

    # ---------------------------------------------------------------- devtools

    def _spawn_dev(self, op: str, *args) -> None:
        """Schedule a DevToolsOps coroutine (results via typed sinks)."""
        coro = getattr(self._devops, op)(*args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _dev_instance(self):
        if self._current_id is None:
            self.show_toast("Select an instance first", "error")
            return None
        inst = get_instance(self._current_id)
        if inst is None:
            self.show_toast("Instance disappeared", "error")
            return None
        return inst

    def _reset_dev_view(self) -> None:
        """Fresh tab per instance (cheap to re-query; never stale)."""
        self._dev_model = ""
        self._dev_meta = {}
        self._dev_records = []
        self._dev_rec_current = None
        self._dev_models_state = SelectionState(multi=False)
        self._dev_recs_state = SelectionState(multi=False)
        sync_model(self._dev_model_rows, [])
        sync_model(self._dev_rec_rows, [])
        sync_strings(self._dev_cron_model, [])
        self.window.dev_model_selected = ""
        self.window.dev_rec_selected = ""
        self.window.dev_rpc_status = "Not connected."
        self.window.dev_models_empty = (
            "Connect to an instance to inspect models.")
        self.window.dev_meta = ""
        self.window.dev_rec_empty = "No query yet — search above."
        self.window.dev_rec_page = "No query yet."
        self.window.dev_cron_empty = "No cron jobs loaded — press Refresh."
        self.window.dev_shell_status = "Shell not running."
        self.window.dev_shell_running = False
        sync_strings(self._dev_shell_model, [])

    def _on_dev_action(self, action: str) -> None:
        """Dispatch grouped DevToolsView actions (Qt page parity)."""
        inst = self._dev_instance()
        if inst is None:
            return
        if action == "rpc-connect":
            self._spawn_dev(
                "rpc_connect", inst.id,
                (self.window.dev_rpc_user or "").strip(),
                self.window.dev_rpc_pass or "",
                bool(self.window.dev_rpc_remember))
        elif action == "rec-search":
            try:
                op = OPERATORS[int(self.window.dev_dom_op_idx)]
            except (IndexError, TypeError, ValueError):
                op = OPERATORS[0]
            self._spawn_dev(
                "rec_search", inst.id, self.window.dev_dom_field or "",
                op, self.window.dev_dom_value or "")
        elif action in ("rec-prev", "rec-next"):
            self._spawn_dev(
                "rec_page", inst.id, -1 if action == "rec-prev" else 1)
        elif action == "rec-new":
            self._rec_new_flow(inst)
        elif action == "rec-edit":
            self._rec_edit_flow(inst)
        elif action == "rec-delete":
            self._rec_delete_flow(inst)
        elif action == "cron-refresh":
            self._spawn_dev("cron_refresh", inst.id)
        elif action == "gen-launch":
            self._spawn_dev("launch_json", inst.id)
        elif action == "open-code":
            self._spawn_dev("open_editor", inst.id, "code")
        elif action == "open-cursor":
            self._spawn_dev("open_editor", inst.id, "cursor")
        elif action == "shell-start":
            self._shell_start_flow(inst)
        elif action == "shell-stop":
            self._shell_stop_flow(inst)
        elif action == "shell-send":
            self._shell_send_flow(inst)
        elif action == "test-run":
            self._test_run_flow(inst)

    def _dev_models_picked(self, model: str) -> None:
        if self._current_id is None:
            return
        self._dev_model = str(model)
        self.window.dev_model_selected = str(model)
        self._dev_models_state.move_current(model)
        self._spawn_dev("model_metadata", self._current_id, str(model))

    def _dev_rec_picked(self, record_id: str) -> None:
        try:
            rid = int(str(record_id))
        except (TypeError, ValueError):
            return
        self._dev_rec_current = rid
        self.window.dev_rec_selected = str(record_id)
        self._dev_recs_state.move_current(str(record_id))

    # ------------------------------------------------------------------ paints

    def _paint_rpc(self, instance_id: str, text: str) -> None:
        if instance_id != self._current_id:
            return
        self.window.dev_rpc_status = text

    def _paint_models(self, instance_id: str, models: list) -> None:
        if instance_id != self._current_id:
            return
        state = self._dev_models_state
        state.set_items([
            {"id": m.get("technical", ""),
             "title": m.get("technical", ""),
             "badge": m.get("display", "")}
            for m in models or [] if m.get("technical")])
        sync_model(self._dev_model_rows, state.view_rows())
        self.window.dev_model_counts = state.counts_text()
        self.window.dev_models_empty = (
            "" if models else "No models found.")

    def _paint_meta(self, instance_id: str, meta: dict) -> None:
        if instance_id != self._current_id:
            return
        self._dev_meta = meta or {}
        self.window.dev_meta = format_meta_line(self._dev_meta)

    def _paint_records(self, instance_id: str, records: list, offset: int,
                       has_more: bool) -> None:
        if instance_id != self._current_id:
            return
        self._dev_records = records or []
        state = self._dev_recs_state
        state.set_items([
            {"id": str(r.get("id", "")),
             "title": format_record_label(r)}
            for r in self._dev_records if r.get("id") is not None])
        sync_model(self._dev_rec_rows, state.view_rows())
        self.window.dev_rec_counts = state.counts_text()
        self.window.dev_rec_empty = (
            "" if self._dev_records else "No records found.")
        page = offset // 50 + 1
        self.window.dev_rec_page = (
            f"Page {page} (offset {offset})"
            + (" — more" if has_more else ""))
        if (self._dev_rec_current is not None and not any(
                r.get("id") == self._dev_rec_current
                for r in self._dev_records)):
            self._dev_rec_current = None
            self.window.dev_rec_selected = ""

    def _paint_crons(self, instance_id: str, crons: list) -> None:
        if instance_id != self._current_id:
            return
        sync_strings(self._dev_cron_model,
                     [format_cron_line(c) for c in crons or []])
        self.window.dev_cron_empty = (
            "" if crons else "No cron jobs found.")

    # ------------------------------------------------------------ record flows

    def _rec_record(self):
        if self._dev_rec_current is None:
            self.show_toast("Select a record first", "error")
            return None
        record = next((r for r in self._dev_records
                       if r.get("id") == self._dev_rec_current), None)
        if record is None:
            self.show_toast("Select a record first", "error")
            return None
        return record

    def _rec_new_flow(self, inst) -> None:
        if not self._dev_model:
            self.show_toast("Pick a model first", "error")
            return
        fields = editable_fields(self._dev_meta, None)
        if not fields:
            self.show_toast("No editable fields loaded — pick a model "
                            "first", "error")
            return
        drv = RecordDriver(
            f"New {self._dev_model}", fields, {},
            on_save=lambda values: self._done_rec_new(
                drv, inst.id, values))
        self._own(drv).show()

    def _done_rec_new(self, drv, instance_id: str, values: dict) -> None:
        self._release(drv)
        if values:
            self._spawn_dev("rec_create", instance_id, values)
        else:
            self.show_toast("No values entered", "error")

    def _rec_edit_flow(self, inst) -> None:
        record = self._rec_record()
        if record is None:
            return
        fields = editable_fields(self._dev_meta, record)
        initial = {f: str(record.get(f, "") or "") for f in fields}
        drv = RecordDriver(
            f"Edit {self._dev_model} #{record.get('id')}", fields,
            initial,
            on_save=lambda values: self._done_rec_edit(
                drv, inst.id, int(record.get("id")), record, values))
        self._own(drv).show()

    def _done_rec_edit(self, drv, instance_id: str, record_id: int,
                       record: dict, values: dict) -> None:
        self._release(drv)
        changed = diff_record_values(record, values)
        if not changed:
            self.show_toast("No changes vs current values", "error")
            return
        preview = "\n".join(f"{k}: {old!r} → {new!r}"
                            for k, (old, new) in changed.items())
        confirm = ConfirmDriver(
            f"Update {self._dev_model} #{record_id}?",
            f"Changed fields:\n{preview}", "Apply update",
            on_result=lambda ok: self._done_rec_update(
                confirm, instance_id, record_id,
                {k: v for k, (_, v) in changed.items()}, ok))
        self._own(confirm).show()

    def _done_rec_update(self, drv, instance_id: str, record_id: int,
                         values: dict, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_dev("rec_update", instance_id, record_id, values)

    def _rec_delete_flow(self, inst) -> None:
        record = self._rec_record()
        if record is None:
            return
        model = self._dev_model
        if model.startswith("ir."):
            self.show_toast(
                f"Refusing to delete from system model '{model}' — "
                "framework metadata rows are off-limits, no exceptions",
                "error")
            return
        shown = record.get("display_name") or record.get("name",
                                                         record.get("id"))
        expected = str(record.get("display_name")
                       or record.get("name", "") or record.get("id"))
        drv = TypedConfirmDriver(
            f"Delete {model} '{shown}'?",
            "This permanently deletes the record. Odoo itself may refuse "
            "when dependents block it — whatever it reports is shown "
            "verbatim. There is no undo.\n\n"
            f"Type {expected!r} to confirm.",
            expected, "Delete permanently",
            on_result=lambda ok: self._done_rec_delete(
                drv, inst.id, int(record.get("id")), expected, ok))
        self._own(drv).show()

    def _done_rec_delete(self, drv, instance_id: str, record_id: int,
                         expected: str, ok: bool) -> None:
        self._release(drv)
        if ok:
            self._spawn_dev("rec_delete", instance_id, record_id, expected)

    # ------------------------------------------------------------------- shell

    def _shell_start_flow(self, inst) -> None:
        old = self._shells.pop(inst.id, None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        session = OdooShell()
        self._track(asyncio.run_coroutine_threadsafe(
            _run_shell_start(self._queue, session, inst.id, inst),
            self._loop))
        self._shells[inst.id] = session
        self._shell_starting.add(inst.id)
        self._shell_lines[inst.id] = []
        sync_strings(self._dev_shell_model, [])
        self.window.dev_shell_status = "Starting shell…"
        self.window.dev_shell_running = True

    def _shell_stop_flow(self, inst) -> None:
        session = self._shells.pop(inst.id, None)
        if session is None:
            return
        self._track(asyncio.run_coroutine_threadsafe(
            _run_shell_stop(self._queue, session, inst.id), self._loop))

    def _shell_send_flow(self, inst) -> None:
        session = self._shells.get(inst.id)
        if session is None:
            self.show_toast("Shell is not running", "error")
            return
        text = self.window.dev_shell_in or ""
        if not text.strip():
            return
        self.window.dev_shell_in = ""
        res = session.send_line(text)
        if not res.ok:
            self.show_toast(res.message, "error")

    def _shell_poll_tick(self) -> None:
        for instance_id, session in list(self._shells.items()):
            if instance_id in self._shell_starting:
                continue
            try:
                lines = session.drain_output()
            except Exception:
                lines = []
            if lines and instance_id == self._current_id:
                buf = self._shell_lines.setdefault(instance_id, [])
                buf.extend(lines)
                del buf[:max(0, len(buf) - 1000)]
                sync_strings(self._dev_shell_model, buf)
            try:
                alive = session.running
            except Exception:
                alive = False
            if not alive:
                self._shells.pop(instance_id, None)
                if instance_id == self._current_id:
                    self.window.dev_shell_status = "Shell exited."
                    self.window.dev_shell_running = False

    # --------------------------------------------------------------- test-run

    def _test_run_flow(self, inst) -> None:
        module = (self.window.dev_test_module or "").strip()
        if not module:
            self.show_toast("Enter the module technical name to test",
                            "error")
            return
        target = (self.window.dev_test_db or "").strip()
        if not target:
            target = f"{(inst.primary_db or 'odoo').strip()}_test"
        if target == (inst.primary_db or "").strip():
            self.show_toast("Refusing to run tests on the PRIMARY "
                            "database — pick a disposable test database",
                            "error")
            return
        drv = ConfirmDriver(
            f"Run {module} tests on '{target}'?",
            "Tests create/modify/destroy data — never the primary DB.",
            "Run tests",
            on_result=lambda ok: self._done_test_run(
                drv, inst.id, module, target, ok))
        self._own(drv).show()

    def _done_test_run(self, drv, instance_id: str, module: str,
                       target: str, ok: bool) -> None:
        self._release(drv)
        if not ok:
            return
        prog = ProgressDriver(f"Tests: {module} on {target}")
        prog.on_close = lambda: self._release(prog)
        self._own(prog).show()
        self.window.dev_busy = True
        factory = lambda emit, cancel: self._modops.run_tests(
            instance_id, target, module, progress_cb=emit, cancel=cancel)
        self._track(asyncio.run_coroutine_threadsafe(
            _run_test_progress(self._queue, prog, factory), self._loop))

    # ----------------------------------------------------------------- wizards

    def _on_wiz_action(self, action: str) -> None:
        if action == "new":
            self._open_create_wizard()
        elif action == "adopt":
            self._open_adopt_wizard()

    def _open_create_wizard(self) -> None:
        holder: dict = {}

        def _branches(branches, message) -> None:
            self._post("wiz-branches", (holder["drv"], branches, message))

        def _syscheck(checks, message) -> None:
            self._post("wiz-syscheck", (holder["drv"], checks, message))

        ops = WizardOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"),
            _branches, _syscheck)
        drv = CreateDriver(
            on_load_branches=lambda: self._track(
                asyncio.run_coroutine_threadsafe(
                    ops.load_branches(), self._loop)),
            on_run_syscheck=lambda version: self._track(
                asyncio.run_coroutine_threadsafe(
                    ops.run_syscheck(version), self._loop)),
            on_provision=lambda draft, plain: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_wiz_provision(self._queue, drv, ops, draft,
                                       plain),
                    self._loop)),
            on_discard=lambda draft_id: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_wiz_discard(self._queue, ops, drv, draft_id),
                    self._loop)),
            on_close=lambda: self._done_wizard(drv))
        holder["drv"] = drv
        self._own(drv).show()

    def _done_wizard(self, drv) -> None:
        self._release(drv)
        self._post_refresh()

    # ------------------------------------------------------- transfer + prefs

    def _spawn_transfer(self, op: str, *args) -> None:
        """Schedule a TransferOps coroutine (message + refresh sinks)."""
        coro = getattr(self._transferops, op)(*args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _on_sys_action(self, action: str) -> None:
        if action == "preferences":
            self._open_preferences()
        elif action == "about":
            self._own(AboutDriver()).show()
        elif action == "import":
            self._track(asyncio.run_coroutine_threadsafe(
                _run_picker(self._queue, "import-pick", "", ""), self._loop))

    def _open_preferences(self) -> None:
        prefs = read_preferences()
        drv = PreferencesDriver(
            prefs["mode"], prefs["keyring_text"], prefs["db_path"],
            on_save=lambda mode: self._done_preferences(
                drv, prefs["mode"], mode))
        self._own(drv).show()

    def _done_preferences(self, drv, current: str, mode: str) -> None:
        self._release(drv)
        if mode == current:
            return
        res = save_preferences(mode)
        self.show_toast(res.message if res.ok else
                        f"Could not save preferences: {res.message}",
                        "info" if res.ok else "error")

    def _export_flow(self) -> None:
        inst = self._current()
        if inst is None:
            self.show_toast("Select an instance first", "error")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self._track(asyncio.run_coroutine_threadsafe(
            _run_picker(self._queue, "export-pick", inst.id,
                        bundle_filename(inst.name, stamp)), self._loop))

    def _continue_export(self, instance_id: str, dest: str) -> None:
        if not dest:
            return
        self._spawn_transfer("export_bundle", instance_id, dest)

    def _continue_import(self, archive: str) -> None:
        if not archive:
            return
        preview = self._transferops.preview(archive)
        if not preview.ok:
            self.show_toast(preview.message, "error")
            return
        info = preview.data or {}
        try:
            port = provisioning.suggest_port(
                int(info.get("port", 8069) or 8069) + 1)
        except Exception:
            port = 8070
        drv = ImportDriver(
            str(info.get("name", "?")),
            str(info.get("version", "?")),
            str(info.get("name", "")),
            port,
            on_import=lambda name, p: self._done_import(
                drv, archive, name, p))
        self._own(drv).show()

    def _done_import(self, drv, archive: str, name: str,
                     port: int) -> None:
        self._release(drv)
        self._spawn_transfer("import_bundle", archive, name, port)

    def _open_scaffold_wizard(self) -> None:
        from odoo_vite.core.registry import list_instances

        try:
            instances = list_instances()
        except Exception:
            instances = []
        entries = [{"id": i.id, "name": i.name,
                    "custom": i.custom_addons_path or "",
                    "primary": i.primary_db or ""}
                   for i in instances]
        if not entries:
            self.show_toast("No instance available for acceptance",
                            "error")
            return
        holder: dict = {}
        ops = WizardOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"))
        drv = ScaffoldDriver(
            entries,
            on_browse_dest=lambda: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_conf_picker(
                        self._queue, "scaffold-dest", holder["drv"]),
                    self._loop)),
            on_build=lambda definition, dest, iid, db: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_wiz_scaffold(self._queue, ops, drv, definition,
                                      dest, iid, db),
                    self._loop)),
            on_close=lambda: self._done_wizard(drv))
        holder["drv"] = drv
        self._own(drv).show()

    def _open_adopt_wizard(self) -> None:
        holder: dict = {}
        ops = WizardOps(
            functools.partial(_wcb, weakref.ref(self), "_post_message"))
        drv = AdoptDriver(
            on_browse_conf=lambda: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_conf_picker(
                        self._queue, "adopt-conf", holder["drv"]),
                    self._loop)),
            on_browse_community=lambda: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_conf_picker(
                        self._queue, "adopt-community", holder["drv"]),
                    self._loop)),
            on_adopt=lambda payload: self._track(
                asyncio.run_coroutine_threadsafe(
                    _run_wiz_adopt(self._queue, ops, drv, payload),
                    self._loop)),
            on_close=lambda: self._done_wizard(drv))
        holder["drv"] = drv
        self._own(drv).show()

    # ------------------------------------------------------------------ logs

    def _spawn_log(self, op: str, *args) -> None:
        """Schedule a LogOps coroutine (results via typed sinks)."""
        coro = getattr(self._logops, op)(*args)
        self._track(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def _start_tail(self, inst) -> None:
        """(Re)start tailing the current instance's log (Qt start_poll
        parity — local, instant, UI thread; the follower is owned by the
        1s timer tick from here on)."""
        path = (inst.log_path or "") if inst is not None else ""
        if not path:
            self._follower = None
            self._tail_lines = []
            sync_strings(self._tail_model, [])
            self.window.log_note = "No log file recorded."
            return
        self._follower = LogFollower(path)
        try:
            initial = read_last_n(path, 500)
        except Exception:
            initial = []
        self._follower.sync_to_end()
        self._tail_lines = [line[:LINE_CAP] for line in initial]
        sync_strings(self._tail_model, self._tail_lines)
        self.window.log_note = (
            f"Tailing {path} (last {len(initial)} lines shown)")

    def _tail_tick(self) -> None:
        # Hidden tabs don't tail (Qt lesson, kept via current-index).
        try:
            if int(self.window.tab_index or 0) != LOGS_TAB:
                return
        except (TypeError, ValueError):
            return
        follower = self._follower
        if follower is None:
            return
        try:
            batch = follower.poll()
        except Exception:
            return
        if batch.get("missing"):
            self.window.log_note = (
                f"Waiting for log file: {follower.path}")
            return
        if batch.get("rotated"):
            self._tail_lines = []
            self.window.log_note = (
                "Log rotated/truncated — restarted from top")
        lines = batch.get("lines", [])
        if lines and self.window.log_follow:
            self._tail_lines.extend(line[:LINE_CAP] for line in lines)
            over = len(self._tail_lines) - LOG_TAIL_CAP
            if over > 0:
                del self._tail_lines[:over]
            sync_strings(self._tail_model, self._tail_lines)

    def _on_log_action(self, action: str) -> None:
        """Dispatch grouped LogsView actions (Qt LogsPage parity)."""
        if self._current_id is None:
            self.show_toast("Select an instance first", "error")
            return
        inst = get_instance(self._current_id)
        if inst is None:
            self.show_toast("Instance disappeared", "error")
            return
        if action == "search":
            try:
                level = LOG_LEVELS[int(self.window.log_level_idx)]
            except (IndexError, TypeError, ValueError):
                level = LOG_LEVELS[0]
            self._spawn_log("search", inst.id,
                            self.window.log_search or "", level)
        elif action == "doctor":
            self._spawn_log("doctor", inst.id)
        elif action == "clear":
            self._tail_lines = []
            sync_strings(self._tail_model, [])
        elif action == "slow-refresh":
            self._spawn_log("slow_refresh", inst.id)
        elif action == "profile":
            try:
                text = PROFILE_DURATIONS[int(self.window.log_profile_idx)]
            except (IndexError, TypeError, ValueError):
                text = "10s"
            self._spawn_log("profile", inst.id,
                            parse_profile_duration(text))

    def _paint_search(self, instance_id: str, ok: bool, message: str,
                      matches: list) -> None:
        if instance_id != self._current_id:
            return
        self.window.log_search_status = message
        sync_strings(self._search_model, format_search_lines(matches))

    def _paint_doctor(self, instance_id: str, findings: list) -> None:
        if instance_id != self._current_id:
            return
        lines = format_doctor_lines(findings)
        sync_strings(self._doctor_model, lines)
        self.window.log_doctor_visible = bool(lines)

    def _paint_slow(self, instance_id: str, ok: bool, message: str,
                    rows: list) -> None:
        if instance_id != self._current_id:
            return
        self.window.log_slow_status = message
        sync_strings(self._slow_model, format_slow_lines(rows))

    def _paint_dev_progress_done(self, driver, ok: bool,
                                   message: str) -> None:
        self.window.dev_busy = False
        if driver in self._dialogs and driver.view is not None:
            driver.done(ok, message)

    def _paint_shell_started(self, instance_id: str,
                             message: str) -> None:
        self._shell_starting.discard(instance_id)
        if instance_id != self._current_id:
            return
        self.window.dev_shell_status = message
        self.window.dev_shell_running = True

    def _paint_shell_start_failed(self, instance_id: str, session,
                                  message: str) -> None:
        self._shell_starting.discard(instance_id)
        if self._shells.get(instance_id) is session:
            self._shells.pop(instance_id, None)
        if instance_id != self._current_id:
            return
        self.window.dev_shell_status = message
        self.window.dev_shell_running = False
        self.show_toast(message, "error")

    def _paint_shell_stopped(self, instance_id: str) -> None:
        if instance_id != self._current_id:
            return
        self.window.dev_shell_status = "Shell stopped."
        self.window.dev_shell_running = False

    def _wiz_owned(self, driver) -> bool:
        return driver in self._dialogs and driver.view is not None

    def _paint_wiz_branches(self, driver, branches: list,
                            message: str) -> None:
        if self._wiz_owned(driver):
            driver.set_branches(branches, message)

    def _paint_wiz_syscheck(self, driver, checks: list,
                            message: str) -> None:
        if self._wiz_owned(driver):
            driver.set_syscheck(checks, message)

    def _paint_wiz_log(self, driver, line: str) -> None:
        if self._wiz_owned(driver):
            driver.append_log(line)

    def _paint_wiz_done(self, driver, instance_id: str, ok: bool,
                        message: str, failed_step: str) -> None:
        self._post_refresh()
        if self._wiz_owned(driver):
            driver.prov_done(ok, message, failed_step)
        if ok:
            self.show_toast(message, "info")
        else:
            self.show_toast(f"{message} (failed at step: {failed_step})",
                            "error")

    def _paint_wiz_discarded(self, driver, ok: bool,
                             message: str) -> None:
        self._release(driver)
        self._post_refresh()
        self.show_toast(message, "info" if ok else "error")

    def _continue_adopt_browse(self, which: str, driver,
                               path: str) -> None:
        if driver not in self._dialogs or driver.view is None:
            return
        if not path:
            return
        if which == "conf":
            driver.set_conf_path(path)
        else:
            driver.set_community_path(path)

    def _paint_wiz_adopt_done(self, driver, ok: bool, message: str,
                              instance_id: str) -> None:
        self._post_refresh()
        if self._wiz_owned(driver):
            driver.adopt_done(ok, message)
        self.show_toast(message, "info" if ok else "error")
        if ok and instance_id:
            self.select(instance_id)

    def _paint_profile(self, instance_id: str, ok: bool, message: str,
                       svg: str) -> None:
        if instance_id != self._current_id:
            return
        self.show_toast(message, "info" if ok else "error")
        if ok and svg:
            self._track(asyncio.run_coroutine_threadsafe(
                open_svg_external(self._queue, svg), self._loop))

    def _spawn_addons_browse(self, drv) -> None:
        self._track(asyncio.run_coroutine_threadsafe(
            _run_conf_picker(self._queue, "addons-pick", drv), self._loop))

    # -------------------------------------------------------------- paint

    def _continue_addons_browse(self, driver, path: str) -> None:
        """zenity blocks — runs on the loop thread, continues on drain."""
        if driver not in self._dialogs or driver.view is None:
            return
        if not path:
            return
        if not addon_paths.looks_like_addons_folder(path):
            self.show_toast(
                "Warning: no subfolder with __manifest__.py found — "
                "you may still add it")
        driver.set_add_path(path)

    def _paint_db_names(self, inst) -> None:
        names = [inst.primary_db] if inst.primary_db else []
        names += [d for d in (inst.tracked_dbs or []) if d not in names]
        sync_strings(self._combo_model, names)
        if self.window.db_picked not in names:
            self.window.db_picked = inst.primary_db or ""
        self._paint_table(inst, self._last_states.get(inst.id, {}))

    def _paint_states(self, instance_id: str, states: dict) -> None:
        self._last_states[instance_id] = states
        if instance_id != self._current_id:
            return
        inst = get_instance(instance_id)
        if inst is not None:
            self._paint_table(inst, states)

    def _paint_table(self, inst, states: dict) -> None:
        """Per-db persistent inner models + outer identity sync: a states
        refresh with identical content writes nothing (no struct drops)."""
        primary = inst.primary_db or ""
        names = [inst.primary_db] if inst.primary_db else []
        names += [d for d in (inst.tracked_dbs or []) if d not in names]
        for gone in list(self._table_cells):
            if gone not in names:
                del self._table_cells[gone]
        for db_name in names:
            info = states.get(db_name, {}) if states else {}
            if info.get("initialized"):
                status = "initialized"
            elif info.get("exists", True) and states:
                status = "exists, not initialized"
            elif states:
                status = "missing"
            else:
                status = "loading…"
            label = f"★ {db_name}" if db_name == primary else db_name
            cells = [
                label,
                info.get("size", "—") or "—",
                f"v{info['odoo_version']}"
                if info.get("odoo_version") else "—",
                status,
            ]
            inner = self._table_cells.get(db_name)
            if inner is None:
                inner = slint.ListModel([{"text": c} for c in cells])
                self._table_cells[db_name] = inner
            else:
                for i, text in enumerate(cells):
                    if dict(inner[i])["text"] != text:
                        inner[i] = {"text": text}
        for i, db_name in enumerate(names):
            inner = self._table_cells[db_name]
            if i < len(self._table_model):
                if self._table_model[i] is not inner:
                    self._table_model[i] = inner
            else:
                self._table_model.append(inner)
        while len(self._table_model) > len(names):
            del self._table_model[len(self._table_model) - 1]

    def _paint_report(self, instance_id: str, report: dict) -> None:
        if instance_id != self._current_id:
            return
        checks = (report or {}).get("checks", [])
        failed = [c for c in checks if c.get("ok") is False]
        self.show_toast(
            f"DB config: {len(checks) - len(failed)} of {len(checks)} "
            f"checks passed" + ("" if not failed else
                                f" — first failure: "
                                f"{failed[0].get('detail', '?')}"))

    def _paint_schedules(self, instance_id: str, scheds: list,
                         status: dict) -> None:
        if instance_id != self._current_id:
            return
        self._sched_ids = [s.id for s in scheds]
        self._sched_objs = {s.id: s for s in scheds}
        rows = []
        for s in scheds:
            state = "on" if s.enabled else "off"
            last = s.last_run or "never"
            rows.append({
                "id": s.id,
                "label": f"{'✓' if s.enabled else '✗'} {s.cron} · "
                         f"{state}",
                "detail": f"{', '.join(s.databases)} · "
                          f"last {last} {s.last_status or ''}".strip(),
            })
        sync_model(self._sched_model, rows)
        try:
            if int(self.window.sched_row) >= len(self._sched_ids):
                self.window.sched_row = -1
        except (TypeError, ValueError):
            self.window.sched_row = -1
        self.window.sched_empty = (
            "" if scheds else "No schedules yet — press Add.")

    def _maybe_probe_server(self) -> None:
        now = time.monotonic()
        if now - self._server_at < 20.0:
            return
        self._server_at = now
        self._track(asyncio.run_coroutine_threadsafe(
            _run_probe(self._queue), self._loop))

    def _paint_server(self, reachable: bool) -> None:
        self.window.server_note = "" if reachable else (
            "⚠ PostgreSQL is unreachable — database operations will fail. "
            "Start the server and it clears automatically.")

    def _paint_overview(self, inst) -> None:
        running = (inst.status or "").lower() == "running"
        self.window.ov_name = inst.name or ""
        self.window.ov_version = inst.version or ""
        self.window.ov_port = str(inst.port or "")
        self.window.ov_path = elide_middle(inst.path or "")
        self.window.ov_path_full = inst.path or ""
        self.window.ov_primary = inst.primary_db or "—"
        self.window.ov_dbuser = inst.db_user or ""
        self.window.ov_status = "Running" if running else "Stopped"
        self.window.is_running = running
        key = (inst.id, inst.enterprise_path or "", inst.version or "")
        if key != self._ent_key:
            self._ent_key = key
            try:
                res = enterprise_core.detect_enterprise(inst)
                data = res.data if res.ok else {}
            except Exception:
                data = {"state": "community"}
            self._ent_text, self._ent_warn = _ent_badge(
                data, inst.version or "")
        self.window.ent_text = self._ent_text
        self.window.ent_warn = self._ent_warn


def _log_failure(fut) -> None:
    try:
        exc = fut.exception()
    except Exception:
        return
    if exc is not None:
        traceback.print_exception(exc)


async def _run_start(ops, queue, iid: str) -> None:
    """First-start probe: needs_confirm comes back as queue data (never a
    blocked thread — Qt parked a worker on an Event for this instead)."""
    res = await ops.start(iid)
    data = res.data or {}
    if data.get("needs_confirm"):
        queue.put(("confirm-start", (iid, data.get("preview", {}))))


async def _run_switch(dbops, queue, iid: str, picked: str) -> None:
    res = await dbops.switch_db(iid, picked)
    data = res.data or {}
    if data.get("needs_confirm"):
        queue.put(("confirm-switch", (iid, picked, data.get("preview", {}))))


async def _run_mod_progress(queue, driver, factory) -> None:
    """Progress plumbing: the driver is UI-thread-owned; worker lines post
    ("progress-line", (driver, line)) into the queue and the drain appends
    them on the UI thread. Cancel flows through the driver's Event, which
    is thread-safe to read from the worker."""

    def _emit(line: str) -> None:
        queue.put(("progress-line", (driver, line)))

    res = await factory(_emit, driver.cancel_event.is_set)
    queue.put(("progress-done", (driver, res.ok, res.message)))


async def _run_test_progress(queue, driver, factory) -> None:
    """ModuleOps.run_tests through the shared progress plumbing, closing
    the DevTools busy gate instead of the Modules one."""

    def _emit(line: str) -> None:
        queue.put(("progress-line", (driver, line)))

    res = await factory(_emit, driver.cancel_event.is_set)
    queue.put(("dev-progress-done", (driver, res.ok, res.message)))


async def _run_shell_start(queue, session, instance_id: str, inst) -> None:
    """Blocking PTY spawn on the loop thread; status lands on drain."""
    res = await asyncio.to_thread(session.start, inst, "")
    if res.ok:
        queue.put(("shell-started", (instance_id, res.message)))
    else:
        queue.put(("shell-start-failed", (instance_id, session,
                                          res.message)))


async def _run_shell_stop(queue, session, instance_id: str) -> None:
    await asyncio.to_thread(session.stop)
    queue.put(("shell-stopped", instance_id))


async def _run_wiz_provision(queue, driver, ops, draft,
                             plaintext: bool) -> None:
    """Provision streams like progress dialogs: worker lines post into
    the queue, the drain appends on the UI thread; Cancel flows through
    the driver's Event."""

    def _emit(line: str) -> None:
        queue.put(("wiz-log", (driver, line)))

    res = await ops.provision(draft, plaintext, progress_cb=_emit,
                              cancel=driver.cancel_event.is_set)
    data = res.data or {}
    queue.put(("wiz-done", (driver, draft.id, res.ok, res.message,
                            data.get("failed_step", "?"))))


async def _run_wiz_discard(queue, ops, driver,
                           instance_id: str) -> None:
    res = await ops.discard_draft(instance_id)
    queue.put(("wiz-discarded", (driver, res.ok, res.message)))


async def _run_wiz_adopt(queue, ops, driver, payload: dict) -> None:
    """Adopt touches no files (registry-only) but stays async like the
    other wizard workers — results land on the drain."""
    res = await ops.adopt_run(payload["name"], payload["conf"],
                              payload["community"],
                              payload["overrides"])
    queue.put(("wiz-adopt-done",
               (driver, res.ok, res.message,
                (res.data or {}).get("id", ""))))


async def _run_wiz_scaffold(queue, ops, driver, definition: dict,
                            dest: str, instance_id: str,
                            db_name: str) -> None:
    """Generate + acceptance install; streams + completion reuse the
    provision queue kinds (the drain guards by driver ownership)."""

    def _emit(line: str) -> None:
        queue.put(("wiz-log", (driver, line)))

    res = await ops.scaffold_install(
        definition, dest, instance_id, db_name, progress_cb=_emit,
        cancel=None)
    queue.put(("wiz-done", (driver, instance_id, res.ok, res.message,
                            "scaffold" if not res.ok else "")))


async def _run_conf_picker(queue, kind: str, driver=None) -> None:
    """zenity blocks — runs on the loop thread, continues on drain."""
    if kind == "python-pick":
        path = await asyncio.to_thread(
            pick_open_file, "Select Python interpreter",
            "All files (*)")
        queue.put(("python-pick", path))
    elif kind == "adopt-conf":
        path = await asyncio.to_thread(
            pick_open_file, "Select odoo.conf",
            "Config files (odoo.conf *.conf)")
        queue.put(("adopt-browse-conf", (driver, path)))
    elif kind == "adopt-community":
        path = await asyncio.to_thread(
            pick_open_dir, "Select community folder")
        queue.put(("adopt-browse-community", (driver, path)))
    elif kind == "scaffold-dest":
        path = await asyncio.to_thread(
            pick_open_dir, "Select destination folder")
        queue.put(("scaffold-dest-pick", (driver, path)))
    else:
        path = await asyncio.to_thread(
            pick_open_dir, "Select addons folder")
        queue.put(("addons-pick", (driver, path)))


async def _run_picker(queue, kind: str, instance_id: str,
                      picked: str) -> None:
    """zenity blocks — runs on the loop thread, continues on drain."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    if kind == "backup-pick":
        path = await asyncio.to_thread(
            _pick_save, f"Back up '{picked}' as…",
            f"{picked}_{stamp}.dump", "Postgres dumps (*.dump)")
        queue.put((kind, (instance_id, picked, path)))
    elif kind == "export-pick":
        path = await asyncio.to_thread(
            _pick_save, "Export instance as…", picked,
            "Odoo Vite bundles (*.tar.gz)")
        queue.put((kind, (instance_id, path)))
    elif kind == "import-pick":
        path = await asyncio.to_thread(
            pick_open_file, "Select instance bundle to import",
            "Odoo Vite bundles (*.tar.gz)")
        queue.put((kind, path))
    else:
        path = await asyncio.to_thread(
            pick_open_file, "Select dump file to restore",
            "Postgres dumps (*.dump *.sql *.sql.gz)")
        queue.put((kind, (instance_id, picked, path)))


async def _run_files(queue, iid: str, name: str) -> None:
    try:
        files = await asyncio.to_thread(
            backup_scheduler.list_backup_files, name)
    except Exception as exc:
        queue.put(("message", (f"Cannot list backups: {exc}", "error")))
        return
    queue.put(("files-ready", (iid, files or [])))


async def _run_probe(queue) -> None:
    try:
        ok = await asyncio.to_thread(_probe_server)
    except Exception:
        ok = True
    queue.put(("server", bool(ok)))


def _probe_server() -> bool:
    """Single pg_isready probe (module fn so tests can stub the process
    boundary without touching threads — see docs/slint-thread-safety.md:
    Slint values and worker threads never coexist in tests)."""
    return bool(db_manager.server_reachable())


def elide_middle(text: str, limit: int = 60) -> str:
    """Qt ConfigurationPage.elided_middle parity: cut the middle so long
    paths don't force tab width. Display-only — the full text stays in
    the matching -full prop for accessible readers."""
    text = text or ""
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    return text[:keep] + "…" + text[-(limit - 1 - keep):]


def _ent_badge(data: dict, version: str) -> tuple[str, bool]:
    state = data.get("state", "community")
    major = data.get("enterprise_major", "")
    if state == "valid" and data.get("match") is True:
        return f"Enterprise {major} ✓ matches Odoo {version}.", False
    if state == "valid" and data.get("match") is False:
        return (f"⚠ Enterprise {major} does NOT match Odoo {version}.",
                True)
    if state == "invalid":
        return "⚠ Enterprise path set but not recognizable.", True
    if state == "valid":
        return "Enterprise set, version unknown — verify.", False
    return "Community edition.", False


def main() -> int:
    bridge = SlintBridge()
    try:
        bridge.run()
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
