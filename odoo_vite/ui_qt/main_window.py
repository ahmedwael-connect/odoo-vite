"""Qt main window shell (PSQ-1.2, wired in PSQ-3): header, sidebar,
stacked content area.

Same information architecture as the GTK MainWindow. Owns: sidebar +
Overview page composition, 2s get_statuses() polling (QThread worker,
queued results), LifecycleFlows controller, toasts. No feature logic.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.core.process_manager import get_statuses
from odoo_vite.core.registry import get_instance, list_instances
from odoo_vite.core.version import __version__
from odoo_vite.ui_qt.flows.lifecycle import LifecycleFlows
from odoo_vite.ui_qt.views.overview import OverviewPage
from odoo_vite.ui_qt.views.sidebar import InstanceSidebar
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm
from odoo_vite.ui_qt.widgets.toasts import Toaster
from odoo_vite.ui_qt.workers import run_in_background

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

        self.stack = QStackedWidget()
        self.overview = OverviewPage(self)
        self.overview.actionRequested.connect(self._on_action)
        self.stack.addWidget(self.overview)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addWidget(QLabel("Select an instance"))
        empty_layout.addStretch(1)
        self.stack.addWidget(empty)
        splitter.addWidget(self.stack)
        splitter.setSizes([260, 740])

        self.flows = LifecycleFlows(self)
        self.flows.message.connect(self._on_flow_message)
        self.flows.refreshRequested.connect(self.refresh_all)
        self.flows.confirmNeeded.connect(self._on_confirm_needed)

        self.statusBar().showMessage("Ready")
        self.refresh_all()
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

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
        def _done(ok: bool, _message: str, data: dict) -> None:
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
        self.stack.setCurrentWidget(self.overview)

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
