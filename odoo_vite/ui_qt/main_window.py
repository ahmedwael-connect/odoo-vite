"""Qt main window shell (PSQ-1.2, wired PSQ-3/PSQ-4): header, sidebar,
tabbed content area.

Owns: sidebar + page composition, 2s get_statuses() polling (QThread
worker, queued results), flow controllers, toasts. No feature logic.
"""

from datetime import datetime, timezone

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402

from odoo_vite.core.process_manager import get_statuses
from odoo_vite.core.registry import (
    get_instance,
    list_instances,
    migrate_password_to_keyring,
)
from odoo_vite.core.version import __version__
from odoo_vite.ui_qt.flows.backup_schedules import BackupSchedulesFlows
from odoo_vite.ui_qt.flows.configuration import ConfigurationFlows
from odoo_vite.ui_qt.flows.databases import DatabaseFlows, group_discover_entries
from odoo_vite.ui_qt.flows.dev_tools_process import DevToolsProcessFlows
from odoo_vite.ui_qt.flows.dev_tools_rpc import DevToolsRpcFlows
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows
from odoo_vite.ui_qt.flows.logs import LogFlows
from odoo_vite.ui_qt.flows.modules import ModuleFlows
from odoo_vite.ui_qt.views.configuration import ConfigurationPage
from odoo_vite.ui_qt.views.databases import DatabasesPage
from odoo_vite.ui_qt.views.devtools import DevToolsPage
from odoo_vite.ui_qt.views.logs import LogsPage
from odoo_vite.ui_qt.views.modules import ModulesPage
from odoo_vite.ui_qt.views.overview import OverviewPage
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm, ask_confirm_typed
from odoo_vite.ui_qt.widgets.selection_list import SelectionList
from odoo_vite.ui_qt.widgets.toasts import Toaster
from odoo_vite.ui_qt.workers import (  # noqa: E402
    BusyTracker, run_in_background, wait_for_background)

POLL_MS = 2000


class QtMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Odoo Vite v{__version__} (Qt)")
        self.resize(1000, 660)
        self._current_id: str | None = None

        toolbar = QToolBar("Main")
        toolbar.addWidget(QLabel(f"Odoo Vite  v{__version__}"))
        self.addToolBar(toolbar)
        self.btn_new = style_button(
            QPushButton("+ New Instance"), "new", primary=True)
        self.btn_new.clicked.connect(self._open_create_wizard)
        toolbar.addWidget(self.btn_new)
        self.btn_adopt = QPushButton("Adopt")
        self.btn_adopt.setToolTip("Take over an existing Odoo installation")
        style_button(self.btn_adopt, "adopt")
        self.btn_adopt.clicked.connect(self._open_adopt_wizard)
        toolbar.addWidget(self.btn_adopt)
        self.btn_events = QPushButton("Events")
        style_button(self.btn_events, "events")
        self.btn_events.setCheckable(True)
        self.btn_events.setToolTip("Application event log (audit trail)")
        self.btn_events.toggled.connect(self._on_events_toggled)
        toolbar.addWidget(self.btn_events)
        self.btn_prefs = QPushButton("Preferences")
        self.btn_prefs.setToolTip(
            "Provisioning mode (Developer vs Managed) and environment")
        self.btn_prefs.clicked.connect(self._open_preferences)
        toolbar.addWidget(self.btn_prefs)
        try:
            file_menu = self.menuBar().addMenu("&File")
            export_action = file_menu.addAction("&Export instance…")
            export_action.triggered.connect(self._export_pick)
            import_action = file_menu.addAction("&Import instance…")
            import_action.triggered.connect(self._import_pick)
            prefs_action = file_menu.addAction("&Preferences…")
            prefs_action.triggered.connect(self._open_preferences)
        except Exception:
            pass

        splitter = QSplitter()
        self.setCentralWidget(splitter)

        self.sidebar = InstanceSidebar(self)
        self.sidebar.setMinimumWidth(200)
        self.sidebar.setMaximumWidth(320)
        self.sidebar.instanceSelected.connect(self._on_select)
        splitter.addWidget(self.sidebar)

        self.stack = QTabWidget()
        self.overview = OverviewPage(self)
        self.overview.actionRequested.connect(self._on_action)
        self.stack.addTab(self.overview, "Overview")
        self.databases = DatabasesPage(self)
        self.databases.actionRequested.connect(self._on_db_action)
        self.stack.addTab(self._scroll_wrap(self.databases), "Databases")
        self.modules = ModulesPage(self)
        self.modules.actionRequested.connect(self._on_mod_action)
        self.stack.addTab(self._scroll_wrap(self.modules), "Modules")
        self.configuration = ConfigurationPage(self)
        self.configuration.actionRequested.connect(self._on_conf_action)
        self.stack.addTab(self._scroll_wrap(self.configuration),
                          "Configuration")
        self.logs = LogsPage(self)
        self.logs.actionRequested.connect(self._on_log_action)
        self.stack.addTab(self.logs, "Logs")
        self.devtools = DevToolsPage(self)
        self.devtools.actionRequested.connect(self._on_dev_action)
        self.stack.addTab(self.devtools, "Dev Tools")
        self.devtools.models_list.selectionChanged.connect(
            self._on_model_picked)
        splitter.addWidget(self.stack)
        splitter.setSizes([260, 740])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.flows = LifecycleFlows(self)
        self.flows.message.connect(self._on_flow_message)
        self.flows.refreshRequested.connect(self.refresh_all)
        self.flows.confirmNeeded.connect(self._on_confirm_needed)
        self.flows.diskReady.connect(self._on_disk_ready)
        self.db_flows = DatabaseFlows(self)
        self.db_flows.message.connect(self._on_flow_message)
        self.db_flows.refreshRequested.connect(self.refresh_all)
        self.db_flows.statesReady.connect(self._on_db_states)
        self.db_flows.reportReady.connect(self._on_validate_report)
        self.mod_flows = ModuleFlows(self)
        self.mod_flows.message.connect(self._on_flow_message)
        self.mod_flows.refreshRequested.connect(self.refresh_all)
        self.mod_flows.modulesReady.connect(self._on_modules_ready)
        self.conf_flows = ConfigurationFlows(self)
        self.conf_flows.message.connect(self._on_flow_message)
        self.conf_flows.refreshRequested.connect(self.refresh_all)
        self.sched_flows = BackupSchedulesFlows(self)
        self.sched_flows.message.connect(self._on_flow_message)
        self.sched_flows.refreshRequested.connect(self.refresh_all)
        self.sched_flows.schedulesReady.connect(self._on_schedules_ready)
        self.log_flows = LogFlows(self)
        self.log_flows.message.connect(self._on_flow_message)
        self.log_flows.searchReady.connect(self._on_search_ready)
        self.log_flows.doctorReady.connect(self._on_doctor_ready)
        self.log_flows.slowReady.connect(self._on_slow_ready)
        self.log_flows.profileDone.connect(self._on_profile_done)
        self.dev_rpc = DevToolsRpcFlows(self)
        self.dev_rpc.message.connect(self._on_flow_message)
        self.dev_rpc.rpcStatus.connect(self._on_rpc_status)
        self.dev_rpc.modelsReady.connect(self._on_models_ready)
        self.dev_rpc.metadataReady.connect(self._on_metadata_ready)
        self.dev_rpc.recordsReady.connect(self._on_records_ready)
        self.dev_rpc.cronsReady.connect(self._on_crons_ready)
        self.dev_proc = DevToolsProcessFlows(self)
        self.dev_proc.message.connect(self._on_flow_message)
        self.dev_proc.shellOutput.connect(self._on_shell_output)
        self.dev_proc.shellStatus.connect(self._on_shell_status)
        self.dev_proc.devmodeState.connect(self._on_devmode_state)
        self.busy_tracker = BusyTracker(self)
        self.busy_tracker.changed.connect(self._on_busy_changed)

        self.statusBar().showMessage("Ready")
        self.stack.setEnabled(False)
        self._closing = False
        # Busy indicator lives in the status bar, NOT the toolbar:
        # QToolBar action-widgets report stale QWidget::isVisible state,
        # which makes show/hide assertions (and possibly styling) lie.
        # Verified live: identical code hides correctly in statusBar.
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)  # indeterminate
        self.busy_bar.setFixedWidth(140)
        self.busy_bar.setVisible(False)
        self.busy_bar.setToolTip("Background operation in progress…")
        self.statusBar().addPermanentWidget(self.busy_bar)
        self._init_event_dock()
        self.refresh_all()
        self._poll_busy = False
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

    def _init_event_dock(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QDockWidget, QListWidget, QPushButton

        from odoo_vite.core import log_tail

        self._event_follower = None
        dock = QDockWidget("Events", self)
        dock.setAllowedAreas(_Qt.BottomDockWidgetArea)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 4, 8, 8)
        self.event_list = QListWidget()
        layout.addWidget(self.event_list)
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        btn_clear = QPushButton("Clear view")
        btn_clear.clicked.connect(self.event_list.clear)
        clear_row.addWidget(btn_clear)
        layout.addLayout(clear_row)
        dock.setWidget(body)
        self.addDockWidget(_Qt.BottomDockWidgetArea, dock)
        dock.setVisible(False)
        # Closing the dock via its own X must uncheck the toggle, or the
        # two drift apart (toggle says open, dock gone).
        dock.visibilityChanged.connect(self._on_dock_visibility)
        self.event_dock = dock
        self._event_timer = QTimer(self)
        self._event_timer.setInterval(1000)
        self._event_timer.timeout.connect(self._event_poll_tick)
        self._event_log_tail = log_tail

    def _open_create_wizard(self) -> None:
        from odoo_vite.ui_qt.wizards.create_instance import CreateWizard

        wiz = CreateWizard(self)
        wiz.instanceCreated.connect(lambda _iid: self.refresh_all())
        wiz.exec()

    def _open_adopt_wizard(self) -> None:
        from odoo_vite.ui_qt.wizards.adopt_instance import AdoptWizard

        wiz = AdoptWizard(self)
        wiz.instanceCreated.connect(lambda _iid: self.refresh_all())
        wiz.exec()

    def _open_preferences(self) -> None:
        from odoo_vite.core import registry as registry_mod
        from odoo_vite.core import settings as settings_mod
        from odoo_vite.ui_qt.widgets.preferences import PreferencesDialog

        try:
            current = settings_mod.get_provisioning_mode()
        except Exception:
            current = "developer"
        try:
            kr_available = bool(registry_mod.keyring_available())
        except Exception:
            kr_available = False
        try:
            db_path = str(registry_mod.get_db_path())
        except Exception:
            db_path = ""
        dlg = PreferencesDialog(self, current, kr_available, db_path)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = dlg.selected_mode()
        if chosen == current:
            return
        res = settings_mod.set_provisioning_mode(chosen)
        self._on_flow_message(
            res.message if res.ok
            else f"Could not save preferences: {res.message}")

    def _open_scaffold_wizard(self) -> None:
        from odoo_vite.ui_qt.wizards.scaffold_module import ScaffoldWizard

        ScaffoldWizard(self).exec()

    def _on_events_toggled(self, show: bool) -> None:
        self.event_dock.setVisible(show)
        if show:
            self._event_start()
        else:
            self._event_timer.stop()
            self._event_follower = None

    def _on_dock_visibility(self, visible: bool) -> None:
        blocked = self.btn_events.signalsBlocked()
        self.btn_events.blockSignals(True)
        self.btn_events.setChecked(visible)
        self.btn_events.blockSignals(blocked)
        if not visible:
            self._event_timer.stop()
            self._event_follower = None

    def _event_start(self) -> None:
        from odoo_vite.core import audit as audit_log

        self._event_timer.stop()
        try:
            follower = self._event_log_tail.LogFollower(
                str(audit_log.audit_path()))
            tail = self._event_log_tail.read_last_n(
                str(audit_log.audit_path()), 100)
        except Exception:
            return
        self._event_follower = follower
        for line in tail:
            self.event_list.addItem(line[:300])
        follower.sync_to_end()
        self._event_timer.start()

    def _event_poll_tick(self) -> None:
        if not self.btn_events.isChecked():
            return
        follower = self._event_follower
        if follower is None:
            return
        try:
            batch = follower.poll()
        except Exception:
            return
        for line in batch.get("lines", [])[-500:]:
            self.event_list.addItem(line[:300])
        while self.event_list.count() > 1000:
            self.event_list.takeItem(0)
        self.event_list.scrollToBottom()

    def closeEvent(self, event) -> None:
        # Never destroy a window with workers in flight: QThread destroyed
        # while running aborts the process. First close parks on a
        # "finishing…" state and retries when drained; a second explicit
        # close forces it (user's call). Long ops (backup/restore/install
        # run minutes) must not be killable by an impatient window close
        # mid-write — hence wait, not terminate.
        from odoo_vite.ui_qt.workers import _LIVE_THREADS

        def _live():
            live = []
            for thread in list(_LIVE_THREADS):
                try:
                    if thread.isRunning():
                        live.append(thread)
                except RuntimeError:
                    pass
            return live

        if _live() and not self._closing:
            self._closing = True
            try:
                self._poll.stop()
            except Exception:
                pass
            self.statusBar().showMessage(
                "Finishing background tasks before closing… "
                "(close again to force)")
            event.ignore()
            QTimer.singleShot(500, self._close_when_idle)
            return
        # No live workers, or second explicit close while waiting: force.
        try:
            self._poll.stop()
        except Exception:
            pass
        try:
            from odoo_vite.ui_qt.workers import wait_for_background
            wait_for_background(timeout_s=20.0)
        except Exception:
            pass
        super().closeEvent(event)

    def _close_when_idle(self) -> None:

        if wait_for_background(timeout_s=120.0):
            self.close()
        else:
            self.statusBar().showMessage(
                "Still waiting on background tasks… (close again to force)")
            QTimer.singleShot(5000, self._close_when_idle)

    # ------------------------------------------------------------------ data

    def refresh_all(self) -> None:
        try:
            instances = list_instances()
        except Exception:
            instances = []
        self.sidebar.set_instances(instances)
        if self._current_id is not None:
            inst = get_instance(self._current_id)
            if inst is not None:
                self.overview.refresh_status(inst)

    def _poll_tick(self) -> None:
        # Overlap guard (GTK _poll_busy parity): a slow poll must not pile
        # workers behind it — and a test/app teardown must never catch a
        # half-finished tick (QThread destroyed while running aborts).
        if self._poll_busy:
            return
        # S3: don't repaint into modal dialogs (a model reset under an open
        # picker drops clicks on dead indexes) or while minimized (nobody
        # watches). Timers keep firing; only the work is skipped.
        try:
            from PySide6.QtWidgets import QApplication as _QApplication
            if _QApplication.activeModalWidget() is not None:
                return
        except Exception:
            pass
        try:
            if self.isMinimized():
                return
        except Exception:
            pass
        self._poll_busy = True

        def _done(ok: bool, _message: str, data: dict) -> None:
            self._poll_busy = False
            for status in data.get("statuses", []):
                if status.get("id") == self._current_id:
                    self.overview.refresh_status(status)
            try:
                self.databases.set_server_status(
                    bool(data.get("server_reachable", True)))
            except Exception:
                pass
            try:
                self.sidebar.set_instances(list_instances())
            except Exception:
                pass

        run_in_background(
            self, _statuses_payload, _done, quiet=True)

    # ------------------------------------------------------------------ flow

    @staticmethod
    def _scroll_wrap(page):
        """Each long tab owns its scroller (REG.3 lesson, Qt edition):
        action buttons below tall lists stay reachable."""
        from PySide6.QtWidgets import QScrollArea

        scrolled = QScrollArea()
        scrolled.setWidgetResizable(True)
        scrolled.setWidget(page)
        return scrolled

    def _on_select(self, instance_id: str) -> None:
        self._current_id = instance_id
        inst = get_instance(instance_id)
        if inst is not None:
            self.overview.show_instance(inst)
            self.databases.show_instance(inst)
            self.modules.show_instance(inst)
            self.configuration.show_instance(inst)
            self.logs.show_instance(inst)
            self.db_flows.refresh_states(instance_id)
            self.sched_flows.refresh_schedules(instance_id)
            self.mod_flows.refresh_modules(instance_id)
        self.stack.setEnabled(True)

    def _on_action(self, action: str, instance_id: str) -> None:
        if action == "start":
            self.flows.start(instance_id)
        elif action == "stop":
            self.flows.stop(instance_id)
        elif action == "restart":
            self.flows.restart(instance_id)
        elif action == "remove":
            self.flows.remove_dialog(self, instance_id)
        elif action == "clone":
            self.flows.clone_dialog(self, instance_id)
        elif action == "rebuild-venv":
            self.flows.rebuild_venv_dialog(self, instance_id)
        elif action == "measure-disk":
            self.flows.measure_disk(instance_id)
        elif action == "browser":
            inst = get_instance(instance_id)
            if inst is not None:
                QDesktopServices.openUrl(
                    QUrl(f"http://localhost:{inst.port}"))
        elif action == "secure":
            self._secure_password(instance_id)

    def _on_db_action(self, action: str, instance_id: str,
                      payload) -> None:
        if action in ("set-primary", "switch"):
            db_name = (payload or "").strip()
            if not db_name:
                self._on_flow_message("Pick a database first")
                return
            if action == "set-primary":
                from odoo_vite.core.registry import update_instance

                res = update_instance(instance_id, primary_db=db_name)
                self._on_flow_message(res.message)
                self.refresh_all()
            else:
                self.flows.switch(instance_id, db_name)
        elif action == "track":
            self.flows.track(instance_id, str(payload or "").strip())
        elif action == "untrack":
            self.flows.untrack(instance_id, str(payload or "").strip())
        elif action == "init-db":
            if payload:
                self.db_flows.init_db(instance_id, str(payload))
        elif action == "backup-db":
            self._backup_picker(instance_id, str(payload or ""))
        elif action == "drop-db":
            db_name = str(payload or "")
            inst = get_instance(instance_id)
            if inst is not None and db_name == (inst.primary_db or ""):
                self._on_flow_message(
                    "The primary database cannot be dropped here — "
                    "switch primary first, or remove the instance")
                return
            if ask_confirm_typed(
                    self, f"Drop database '{db_name}'?",
                    "This permanently deletes the database. The instance "
                    "keeps running on its primary.",
                    db_name, "Drop permanently"):
                self.db_flows.drop_db(instance_id, db_name)
        elif action == "restore":
            self._restore_picker(instance_id)
        elif action == "validate":
            self.db_flows.validate(instance_id)
        elif action == "refresh-states":
            self.db_flows.refresh_states(instance_id)
        elif action == "discover":
            self._discover_dialog(instance_id)
        elif action == "sched-add":
            self.sched_flows.sched_add_dialog(self, instance_id)
        elif action == "sched-edit":
            self.sched_flows.sched_edit_dialog(
                self, instance_id, str(payload or ""))
        elif action == "sched-delete":
            self.sched_flows.sched_delete(
                self, instance_id, str(payload or ""))
        elif action == "sched-toggle":
            self.sched_flows.sched_toggle(instance_id, str(payload or ""))
        elif action == "sched-run-now":
            self.sched_flows.sched_run_now(instance_id, str(payload or ""))
        elif action == "backups-browse":
            self.sched_flows.backups_browse_dialog(self, instance_id)

    def _on_schedules_ready(self, instance_id: str, scheds: list,
                            status: dict) -> None:
        if instance_id == self._current_id:
            self.databases.refresh_schedules(scheds, status)

    def _backup_picker(self, instance_id: str, db_name: str) -> None:
        if not db_name:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest, _ = QFileDialog.getSaveFileName(
            self, f"Back up '{db_name}' as…", f"{db_name}_{stamp}.dump",
            "Postgres dumps (*.dump)")
        if dest:
            self.db_flows.backup_db(instance_id, db_name, dest)

    def _restore_picker(self, instance_id: str) -> None:
        dump, _ = QFileDialog.getOpenFileName(
            self, "Select dump file to restore", "",
            "Postgres dumps (*.dump *.sql *.sql.gz)")
        if not dump:
            return
        inst = get_instance(instance_id)
        target, ok = QInputDialog.getText(
            self, "Restore database",
            "Target database (will be DROPPED and recreated — never merged):",
            text=(inst.primary_db if inst else ""))
        target = (target or "").strip()
        if not ok or not target:
            return
        if ask_confirm_typed(
                self, "Restore database?",
                f"Retype the target name to restore into '{target}'.",
                target, "Restore (drop + recreate)"):
            self.db_flows.restore_db(instance_id, dump, target)

    def _export_pick(self) -> None:
        """U5.5: bundle the selected instance to a .tar.gz file."""
        if self._current_id is None:
            self._on_flow_message("Select an instance first")
            return
        inst = get_instance(self._current_id)
        if inst is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = "".join(
            c if c.isalnum() else "_" for c in (inst.name or "instance"))
        dest, _ = QFileDialog.getSaveFileName(
            self, f"Export '{inst.name}' as…", f"{slug}_{stamp}.tar.gz",
            "Odoo Vite bundles (*.tar.gz)")
        if dest:
            self.flows.export_bundle(self._current_id, dest)

    def _import_pick(self) -> None:
        """U5.5: pick a bundle, then import it as a new instance."""
        archive, _ = QFileDialog.getOpenFileName(
            self, "Select instance bundle to import", "",
            "Odoo Vite bundles (*.tar.gz)")
        if archive:
            self.flows.import_dialog(self, archive)

    def _on_db_states(self, instance_id: str, states: dict) -> None:
        if instance_id == self._current_id:
            self.databases.set_db_states(states)

    def _on_validate_report(self, instance_id: str, report: dict) -> None:
        if instance_id != self._current_id:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Config validation")
        dlg.setMinimumWidth(480)
        layout = QVBoxLayout(dlg)
        checks = (report or {}).get("checks", [])
        failed = [c for c in checks if c.get("ok") is False]
        summary = QLabel(
            f"{len(checks) - len(failed)} of {len(checks)} checks passed")
        summary.setProperty("class",
                            "success" if not failed else "warning")
        layout.addWidget(summary)
        rows = QListWidget()
        for check in checks:
            icon = ("✅" if check.get("ok") is True
                    else ("❌" if check.get("ok") is False else "❔"))
            rows.addItem(f"{icon}  {check.get('field', '?')} — "
                         f"{check.get('detail', '')}")
        layout.addWidget(rows)
        close_btn = QPushButton("Close", dlg)
        layout.addWidget(close_btn)
        dlg.exec()

    def _discover_dialog(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        version = inst.version or ""
        self._on_flow_message("Discovering databases…")

        def _on_ready(payload: dict) -> None:
            if not payload.get("ok"):
                self._on_flow_message(payload.get("message", "Discover failed"))
                return
            groups = group_discover_entries(payload.get("entries", []),
                                            version)
            rows = []
            for group, names in (("Likely this version", groups["likely"]),
                                 ("Other versions", groups["other"]),
                                 ("Uninitialized", groups["plain"])):
                if not names:
                    continue
                rows.append({"id": f"__group:{group}", "title": group,
                             "header": True})
                for name in names:
                    rows.append({"id": name, "title": name,
                                 "badge": group,
                                 # A.1 parity: ONLY likely arrives checked.
                                 "group": group,
                                 "checked": group == "Likely this version"})
            dlg = QDialog(self)
            dlg.setWindowTitle("Discover databases")
            dlg.setMinimumWidth(520)
            layout = QVBoxLayout(dlg)
            picker = SelectionList(multi=True, parent=dlg)
            picker.set_items(rows)
            layout.addWidget(picker)
            btn_row = QHBoxLayout()
            btn_track = QPushButton("Track selected", dlg)
            btn_cancel = QPushButton("Cancel", dlg)
            btn_row.addWidget(btn_track)
            btn_row.addWidget(btn_cancel)
            layout.addLayout(btn_row)
            btn_track.clicked.connect(dlg.accept)
            btn_cancel.clicked.connect(dlg.reject)
            if dlg.exec() != QDialog.Accepted:
                return
            self.flows.track_many(instance_id, picker.checked_ids())

        self.flows.discover_entries(instance_id, _on_ready)

        
    def _on_modules_ready(self, instance_id: str, modules: list,
                          diff: dict, error: str) -> None:
        if instance_id == self._current_id:
            self.modules.set_modules(modules, diff, error)

    def _on_mod_action(self, action: str, instance_id: str,
                       payload) -> None:
        if action == "mod-refresh":
            self.mod_flows.refresh_modules(instance_id)
        elif action == "mod-install":
            self.mod_flows.install(instance_id, payload or [])
        elif action == "mod-update":
            self.mod_flows.update(instance_id, payload or [])
        elif action == "mod-uninstall":
            self.mod_flows.uninstall(instance_id, str(payload or ""))
        elif action == "mod-update-code":
            self.mod_flows.update_code(instance_id)
        elif action == "mod-deps":
            self.mod_flows.show_deps(instance_id, str(payload or ""))
        elif action == "mod-scaffold":
            self._open_scaffold_wizard()

    def _on_conf_action(self, action: str, instance_id: str,
                        payload) -> None:
        if action == "conf-save":
            self.conf_flows.save(instance_id, payload or {})
        elif action == "conf-restore":
            self.conf_flows.restore(instance_id)
        elif action == "conf-regenerate":
            self.conf_flows.regenerate(instance_id)
        elif action == "meta-save":
            self.conf_flows.meta_save(instance_id, payload or {})
        elif action == "addons-manage":
            self.conf_flows.addons_manage(self, instance_id)

    def _on_log_action(self, action: str, instance_id: str,
                       payload) -> None:
        if action == "log-search":
            pattern = self.logs.entry_search.text()
            level = self.logs.drop_level.currentText()
            level = None if level == "All levels" else level
            self.log_flows.search(instance_id, pattern, level)
        elif action == "log-doctor":
            self.log_flows.doctor(instance_id)
        elif action == "slow-refresh":
            self.log_flows.slow_refresh(instance_id)
        elif action == "profile":
            self.log_flows.profile(
                instance_id, int(payload or 10))

    def _on_search_ready(self, instance_id: str, ok: bool, message: str,
                         matches: list) -> None:
        if instance_id == self._current_id:
            self.logs.set_search_results(matches if ok else [], message)

    def _on_doctor_ready(self, instance_id: str, findings: list) -> None:
        if instance_id == self._current_id:
            self.logs.set_doctor_findings(findings)

    def _on_slow_ready(self, instance_id: str, ok: bool, message: str,
                       rows: list) -> None:
        if instance_id == self._current_id:
            self.logs.set_slow_queries(ok, message, rows)

    def _on_profile_done(self, instance_id: str, ok: bool, message: str,
                         svg: str) -> None:
        self._on_flow_message(message)
        if ok and svg:
            self.log_flows.view_svg(svg)

    def _on_dev_action(self, action: str, instance_id: str,
                       payload) -> None:
        payload = payload or {}
        if action == "rpc-connect":
            self.dev_rpc.connect(
                instance_id, payload.get("user", ""),
                payload.get("password", ""),
                bool(payload.get("remember", False)))
        elif action == "model-selected":
            self.dev_rpc.model_selected(instance_id, str(payload or ""))
        elif action == "rec-search":
            self.devtools_rec_search(instance_id)
        elif action == "rec-prev":
            self.dev_rpc.rec_page(instance_id, -1)
        elif action == "rec-next":
            self.dev_rpc.rec_page(instance_id, 1)
        elif action == "rec-new":
            self.dev_rpc.rec_new(self, instance_id)
        elif action == "rec-edit":
            record_id = self.devtools.records_list.current_id()
            if record_id is not None:
                self.dev_rpc.rec_edit(self, instance_id, record_id)
            else:
                self._on_flow_message("Select a record first")
        elif action == "rec-delete":
            record_id = self.devtools.records_list.current_id()
            if record_id is not None:
                self.dev_rpc.rec_delete(self, instance_id, record_id)
            else:
                self._on_flow_message("Select a record first")
        elif action == "cron-refresh":
            self.dev_rpc.cron_refresh(instance_id)
        elif action == "gen-launch":
            self.dev_rpc.launch_json(instance_id)
        elif action == "open-code":
            self.dev_rpc.open_editor(instance_id, "code")
        elif action == "open-cursor":
            self.dev_rpc.open_editor(instance_id, "cursor")
        elif action == "shell-start":
            self.dev_proc.shell_start(instance_id)
        elif action == "shell-send":
            self.dev_proc.shell_send(instance_id, str(payload or ""))
        elif action == "shell-stop":
            self.dev_proc.shell_stop(instance_id)
        elif action == "devmode":
            self.dev_proc.devmode(instance_id, bool(payload))
        elif action == "test-run":
            self.dev_proc.test_run(
                instance_id, str(payload.get("module", "")),
                str(payload.get("db", "")))

    def _on_model_picked(self) -> None:
        if self._current_id is None:
            return
        model = self.devtools.models_list.selected_id()
        if model:
            self.dev_rpc.model_selected(self._current_id, model)

    def devtools_rec_search(self, instance_id: str) -> None:
        self.dev_rpc.rec_search(
            instance_id, self.devtools.entry_dom_field.text(),
            self.devtools.drop_dom_op.currentText(),
            self.devtools.entry_dom_value.text())

    def _on_rpc_status(self, instance_id: str, text: str) -> None:
        if instance_id == self._current_id:
            self.devtools.lbl_rpc_status.setText(text)

    def _on_models_ready(self, instance_id: str, models: list) -> None:
        if instance_id != self._current_id:
            return
        self.devtools.set_models([
            {"id": m.get("technical", ""), "title": m.get("technical", ""),
             "badge": m.get("display", "")} for m in models])

    def _on_metadata_ready(self, instance_id: str, meta: dict) -> None:
        if instance_id != self._current_id:
            return
        self.devtools.meta_list.clear()
        for field in meta.get("fields", []):
            self.devtools.meta_list.addItem(
                f"{field.get('name', '')} ({field.get('ttype', '')})")
        self.devtools.lbl_model_meta.setText(
            f"{meta.get('model', '')}: "
            f"{len(meta.get('fields', []))} fields, "
            f"{len(meta.get('constraints', []))} constraints")

    def _on_records_ready(self, instance_id: str, records: list,
                          offset: int, has_more: bool) -> None:
        if instance_id != self._current_id:
            return
        self.devtools.set_records([
            {"id": r.get("id"),
             "title": str(r.get("display_name") or r.get("name", r.get("id"))),
             "badge": ""}
            for r in records])
        self.devtools.lbl_rec_page.setText(
            f"Offset {offset} — {len(records)} row(s)"
            + (" (more…)" if has_more else ""))

    def _on_crons_ready(self, instance_id: str, crons: list) -> None:
        if instance_id != self._current_id:
            return
        self.devtools.set_crons(crons)

    def _on_shell_output(self, instance_id: str, lines: list) -> None:
        if instance_id == self._current_id:
            for line in lines:
                self.devtools.shell_append(line)

    def _on_shell_status(self, instance_id: str, text: str) -> None:
        if instance_id == self._current_id:
            self.devtools.shell_set_status(text)
        self._on_flow_message(text)

    def _on_devmode_state(self, instance_id: str, on: bool,
                          note: str) -> None:
        if instance_id == self._current_id:
            self.devtools.set_devmode_state(on, note)

    def _secure_password(self, instance_id: str) -> None:
        """H.2 one-click sweep: move the password to the OS keyring."""
        self._on_flow_message("Securing password…")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self._on_flow_message(message)
            self.refresh_all()

        run_in_background(self, migrate_password_to_keyring, _done,
                          instance_id)

    def _on_busy_changed(self, busy: bool) -> None:
        """GTK _op_start/_op_end parity: spinner + desensitized actions."""
        self.busy_bar.setVisible(busy)
        for page in (self.overview, self.databases, self.modules,
                     self.configuration, self.logs, self.devtools):
            try:
                page.set_actions_enabled(not busy)
            except Exception:
                pass
        try:
            self.btn_new.setEnabled(not busy)
            self.btn_adopt.setEnabled(not busy)
        except Exception:
            pass

    def _on_confirm_needed(self, payload: dict) -> None:
        confirmed = ask_confirm(self, payload.get("heading", "Confirm"),
                                payload.get("body", ""), "Confirm")
        self.flows.reply_confirm(payload.get("key", ""), confirmed)

    def _on_disk_ready(self, instance_id: str, text: str) -> None:
        if instance_id == self._current_id:
            try:
                self.overview.set_disk_text(text)
            except Exception:
                pass

    def _on_flow_message(self, message: str) -> None:
        try:
            self.statusBar().showMessage(message, 5000)
        except Exception:
            pass
        try:
            Toaster.show_text(self, message)
        except Exception:
            pass


_REACH_TTL_S = 20.0
_REACH_CACHE = {"at": 0.0, "value": True}


def _statuses_payload() -> object:
    from time import monotonic

    from odoo_vite.core.result import Result

    try:
        statuses = get_statuses()
    except Exception as exc:
        return Result.failure(str(exc))
    # pg_isready costs ~50ms per spawn — cache briefly (UI hint only; all
    # verify-then-act paths probe live in core and never see this value).
    now = monotonic()
    if now - _REACH_CACHE["at"] >= _REACH_TTL_S:
        try:
            from odoo_vite.core.db_manager import server_reachable
            _REACH_CACHE["value"] = bool(server_reachable())
        except Exception:
            _REACH_CACHE["value"] = True  # unknown: don't banner on probe failure
        _REACH_CACHE["at"] = now
    return Result(ok=True, message="",
                  data={"statuses": statuses,
                        "server_reachable": _REACH_CACHE["value"]})
