"""Qt main window shell (PSQ-1.2, wired PSQ-3/PSQ-4): header, sidebar,
tabbed content area.

Same information architecture as the GTK MainWindow. Owns: sidebar +
page composition, 2s get_statuses() polling (QThread worker, queued
results), flow controllers, toasts. No feature logic.
"""

from datetime import datetime, timezone

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.core.process_manager import get_statuses
from odoo_vite.core.registry import get_instance, list_instances
from odoo_vite.core.version import __version__
from odoo_vite.ui_qt.flows.databases import DatabaseFlows, group_discover_entries
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows
from odoo_vite.ui_qt.flows.modules import ModuleFlows
from odoo_vite.ui_qt.views.databases import DatabasesPage
from odoo_vite.ui_qt.views.modules import ModulesPage
from odoo_vite.ui_qt.views.overview import OverviewPage
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm, ask_confirm_typed
from odoo_vite.ui_qt.widgets.selection_list import SelectionList
from odoo_vite.ui_qt.widgets.toasts import Toaster
from odoo_vite.ui_qt.workers import (  # noqa: E402
    run_in_background, wait_for_background)

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

        splitter = QSplitter()
        self.setCentralWidget(splitter)

        self.sidebar = InstanceSidebar(self)
        self.sidebar.setMaximumWidth(320)
        self.sidebar.instanceSelected.connect(self._on_select)
        splitter.addWidget(self.sidebar)

        self.stack = QTabWidget()
        self.overview = OverviewPage(self)
        self.overview.actionRequested.connect(self._on_action)
        self.stack.addTab(self.overview, "Overview")
        self.databases = DatabasesPage(self)
        self.databases.actionRequested.connect(self._on_db_action)
        self.stack.addTab(self.databases, "Databases")
        self.modules = ModulesPage(self)
        self.modules.actionRequested.connect(self._on_mod_action)
        self.stack.addTab(self.modules, "Modules")
        splitter.addWidget(self.stack)
        splitter.setSizes([260, 740])

        self.flows = LifecycleFlows(self)
        self.flows.message.connect(self._on_flow_message)
        self.flows.refreshRequested.connect(self.refresh_all)
        self.flows.confirmNeeded.connect(self._on_confirm_needed)
        self.db_flows = DatabaseFlows(self)
        self.db_flows.message.connect(self._on_flow_message)
        self.db_flows.refreshRequested.connect(self.refresh_all)
        self.db_flows.statesReady.connect(self._on_db_states)
        self.db_flows.reportReady.connect(self._on_validate_report)
        self.mod_flows = ModuleFlows(self)
        self.mod_flows.message.connect(self._on_flow_message)
        self.mod_flows.refreshRequested.connect(self.refresh_all)
        self.mod_flows.modulesReady.connect(self._on_modules_ready)

        self.statusBar().showMessage("Ready")
        self.stack.setEnabled(False)
        self.refresh_all()
        self._poll_busy = False
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

    def closeEvent(self, event) -> None:
        # Never destroy a window with workers in flight: QThread destroyed
        # while running aborts the process (verified live at app quit).
        try:
            self._poll.stop()
        except Exception:
            pass
        try:
            wait_for_background(timeout_s=20.0)
        except Exception:
            pass
        super().closeEvent(event)

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
        self._poll_busy = True

        def _done(ok: bool, _message: str, data: dict) -> None:
            self._poll_busy = False
            for status in data.get("statuses", []):
                if status.get("id") == self._current_id:
                    self.overview.refresh_status(status)
            try:
                self.sidebar.set_instances(list_instances())
            except Exception:
                pass

        run_in_background(
            self, _statuses_payload, _done)

    # ------------------------------------------------------------------ flow

    def _on_select(self, instance_id: str) -> None:
        self._current_id = instance_id
        inst = get_instance(instance_id)
        if inst is not None:
            self.overview.show_instance(inst)
            self.databases.show_instance(inst)
            self.modules.show_instance(inst)
            self.db_flows.refresh_states(instance_id)
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
            inst = get_instance(instance_id)
            name = inst.name if inst else instance_id
            if ask_confirm(self, "Remove instance?",
                           f"Remove {name} from Odoo Vite?",
                           "Remove", destructive=False):
                # Destructive tier parity: typed confirm for remove.
                from odoo_vite.ui_qt.widgets.dialogs import (
                    ask_confirm_typed,
                )
                if ask_confirm_typed(
                        self, "Remove instance?",
                        f"Type the instance name to remove {name}.",
                        name, "Remove"):
                    self.flows.remove(instance_id)

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

    def _on_confirm_needed(self, payload: dict) -> None:
        confirmed = ask_confirm(self, payload.get("heading", "Confirm"),
                                payload.get("body", ""), "Confirm")
        self.flows.reply_confirm(payload.get("key", ""), confirmed)

    def _on_flow_message(self, message: str) -> None:
        try:
            self.statusBar().showMessage(message, 5000)
        except Exception:
            pass
        try:
            Toaster.show_text(self, message)
        except Exception:
            pass


def _statuses_payload() -> object:
    from odoo_vite.core.result import Result

    try:
        statuses = get_statuses()
    except Exception as exc:
        return Result.failure(str(exc))
    return Result(ok=True, message="", data={"statuses": statuses})
