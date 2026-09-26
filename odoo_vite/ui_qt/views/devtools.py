"""Dev Tools page (PSQ-8.2): faithful port of the GTK Dev Tools tab.

Long content lives in a QScrollArea (the tab itself doesn't scroll).
Sections: RPC connect, Model Inspector, Record Browser, Cron Jobs,
launch.json/editors, Shell REPL, Dev Mode Watch, module test runner.
Emits actionRequested; DevToolsRpcFlows/DevToolsProcessFlows execute.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.selection_list import SelectionList

OPERATORS = ["=", "!=", "like", "ilike", ">", "<", ">=", "<="]


class DevToolsPage(QWidget):
    actionRequested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scrolled = QScrollArea()
        scrolled.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)
        scrolled.setWidget(body)
        outer.addWidget(scrolled)

        # ---------------------------------------------------------- RPC
        rpc_title = QLabel("RPC connection")
        rpc_title.setProperty("class", "heading")
        layout.addWidget(rpc_title)
        rpc_row = QHBoxLayout()
        rpc_row.setSpacing(8)
        self.entry_rpc_user = QLineEdit()
        self.entry_rpc_user.setPlaceholderText("Odoo user (default admin)")
        rpc_row.addWidget(self.entry_rpc_user, 1)
        self.entry_rpc_pass = QLineEdit()
        self.entry_rpc_pass.setPlaceholderText("password")
        self.entry_rpc_pass.setEchoMode(QLineEdit.Password)
        rpc_row.addWidget(self.entry_rpc_pass, 1)
        self.check_rpc_remember = QCheckBox("Remember")
        rpc_row.addWidget(self.check_rpc_remember)
        self.btn_rpc_connect = QPushButton("Connect")
        self.btn_rpc_connect.clicked.connect(self._on_rpc_connect)
        rpc_row.addWidget(self.btn_rpc_connect)
        layout.addLayout(rpc_row)
        self.lbl_rpc_status = QLabel("Not connected.")
        self.lbl_rpc_status.setProperty("class", "dim")
        self.lbl_rpc_status.setWordWrap(True)
        layout.addWidget(self.lbl_rpc_status)

        # ---------------------------------------------------------- models
        models_title = QLabel("Model Inspector")
        models_title.setProperty("class", "heading")
        layout.addWidget(models_title)
        self.models_list = SelectionList(multi=False, parent=self)
        layout.addWidget(self.models_list)
        self.meta_list = QListWidget()
        self.meta_list.setMaximumHeight(150)
        layout.addWidget(self.meta_list)
        self.lbl_model_meta = QLabel()
        self.lbl_model_meta.setProperty("class", "dim")
        layout.addWidget(self.lbl_model_meta)

        # ---------------------------------------------------------- records
        rec_title = QLabel("Record Browser")
        rec_title.setProperty("class", "heading")
        layout.addWidget(rec_title)
        dom_row = QHBoxLayout()
        dom_row.setSpacing(8)
        self.entry_dom_field = QLineEdit()
        self.entry_dom_field.setPlaceholderText("field (empty = all)")
        dom_row.addWidget(self.entry_dom_field, 1)
        self.drop_dom_op = QComboBox()
        self.drop_dom_op.addItems(OPERATORS)
        dom_row.addWidget(self.drop_dom_op)
        self.entry_dom_value = QLineEdit()
        self.entry_dom_value.setPlaceholderText("value")
        dom_row.addWidget(self.entry_dom_value, 1)
        self.btn_rec_search = QPushButton("Search")
        self.btn_rec_search.clicked.connect(
            lambda: self._emit("rec-search", None))
        dom_row.addWidget(self.btn_rec_search)
        layout.addLayout(dom_row)
        self.records_list = SelectionList(multi=False, parent=self)
        layout.addWidget(self.records_list)
        rec_nav = QHBoxLayout()
        rec_nav.setSpacing(8)
        self.btn_rec_prev = QPushButton("◀ Prev")
        self.btn_rec_prev.clicked.connect(
            lambda: self._emit("rec-prev", None))
        rec_nav.addWidget(self.btn_rec_prev)
        self.lbl_rec_page = QLabel("No query yet.")
        self.lbl_rec_page.setProperty("class", "dim")
        rec_nav.addWidget(self.lbl_rec_page, 1)
        self.btn_rec_next = QPushButton("Next ▶")
        self.btn_rec_next.clicked.connect(
            lambda: self._emit("rec-next", None))
        rec_nav.addWidget(self.btn_rec_next)
        layout.addLayout(rec_nav)
        rec_ops = QHBoxLayout()
        rec_ops.setSpacing(8)
        self.btn_rec_new = QPushButton("New…")
        self.btn_rec_new.clicked.connect(
            lambda: self._emit("rec-new", None))
        rec_ops.addWidget(self.btn_rec_new)
        self.btn_rec_edit = QPushButton("Edit…")
        self.btn_rec_edit.clicked.connect(
            lambda: self._emit("rec-edit", None))
        rec_ops.addWidget(self.btn_rec_edit)
        self.btn_rec_delete = QPushButton("Delete…")
        self.btn_rec_delete.setProperty("role", "destructive")
        self.btn_rec_delete.clicked.connect(
            lambda: self._emit("rec-delete", None))
        rec_ops.addWidget(self.btn_rec_delete)
        rec_ops.addStretch(1)
        layout.addLayout(rec_ops)

        # ---------------------------------------------------------- cron
        cron_title = QLabel("Cron Jobs")
        cron_title.setProperty("class", "heading")
        layout.addWidget(cron_title)
        cron_row = QHBoxLayout()
        cron_row.setSpacing(8)
        self.btn_cron_refresh = QPushButton("Refresh")
        self.btn_cron_refresh.clicked.connect(
            lambda: self._emit("cron-refresh", None))
        cron_row.addWidget(self.btn_cron_refresh)
        cron_row.addStretch(1)
        layout.addLayout(cron_row)
        self.cron_list = QListWidget()
        self.cron_list.setMaximumHeight(150)
        layout.addWidget(self.cron_list)

        # ---------------------------------------------------------- launch/editors
        tool_title = QLabel("Launch & editors")
        tool_title.setProperty("class", "heading")
        layout.addWidget(tool_title)
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.btn_launch_json = QPushButton("Generate launch.json")
        self.btn_launch_json.clicked.connect(
            lambda: self._emit("gen-launch", None))
        tool_row.addWidget(self.btn_launch_json)
        self.btn_open_code = QPushButton("Open in VS Code")
        self.btn_open_code.clicked.connect(
            lambda: self._emit("open-code", None))
        tool_row.addWidget(self.btn_open_code)
        self.btn_open_cursor = QPushButton("Open in Cursor")
        self.btn_open_cursor.clicked.connect(
            lambda: self._emit("open-cursor", None))
        tool_row.addWidget(self.btn_open_cursor)
        tool_row.addStretch(1)
        layout.addLayout(tool_row)

        # ---------------------------------------------------------- shell
        shell_title = QLabel("Odoo Shell (PTY REPL)")
        shell_title.setProperty("class", "heading")
        layout.addWidget(shell_title)
        shell_row = QHBoxLayout()
        shell_row.setSpacing(8)
        self.btn_shell_start = QPushButton("Start shell")
        self.btn_shell_start.clicked.connect(
            lambda: self._emit("shell-start", None))
        shell_row.addWidget(self.btn_shell_start)
        self.btn_shell_stop = QPushButton("Stop")
        self.btn_shell_stop.clicked.connect(
            lambda: self._emit("shell-stop", None))
        shell_row.addWidget(self.btn_shell_stop)
        self.lbl_shell_status = QLabel("Shell not running.")
        self.lbl_shell_status.setProperty("class", "dim")
        shell_row.addWidget(self.lbl_shell_status, 1)
        layout.addLayout(shell_row)
        self.shell_output = QTextEdit()
        self.shell_output.setReadOnly(True)
        self.shell_output.setFontFamily("monospace")
        self.shell_output.setMinimumHeight(200)
        self.shell_output.setMaximumHeight(320)
        layout.addWidget(self.shell_output)
        self.entry_shell_in = QLineEdit()
        self.entry_shell_in.setPlaceholderText(
            "Type Python to run in the shell… (Enter sends)")
        self.entry_shell_in.returnPressed.connect(self._on_shell_send)
        layout.addWidget(self.entry_shell_in)

        # ---------------------------------------------------------- devmode
        dev_title = QLabel("Dev Mode Watch")
        dev_title.setProperty("class", "heading")
        layout.addWidget(dev_title)
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        self.check_devmode = QCheckBox("Watch addon files, auto-restart")
        self.check_devmode.toggled.connect(self._on_devmode_toggled)
        dev_row.addWidget(self.check_devmode)
        self.lbl_devmode = QLabel()
        self.lbl_devmode.setProperty("class", "dim")
        dev_row.addWidget(self.lbl_devmode, 1)
        layout.addLayout(dev_row)

        # ---------------------------------------------------------- tests
        test_title = QLabel("Module tests")
        test_title.setProperty("class", "heading")
        layout.addWidget(test_title)
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        self.entry_test_module = QLineEdit()
        self.entry_test_module.setPlaceholderText("module name")
        test_row.addWidget(self.entry_test_module, 1)
        self.entry_test_db = QLineEdit()
        self.entry_test_db.setPlaceholderText("test database (never primary!)")
        test_row.addWidget(self.entry_test_db, 1)
        self.btn_test_run = QPushButton("Run tests")
        self.btn_test_run.clicked.connect(self._on_test_run)
        test_row.addWidget(self.btn_test_run)
        layout.addLayout(test_row)
        layout.addStretch(1)

    # ------------------------------------------------------------------ API

    def show_instance(self, instance) -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
        else:
            self._instance_id = instance.id

    # -------------------------------------------------------------- internals

    def _on_rpc_connect(self) -> None:
        self._emit("rpc-connect", {
            "user": self.entry_rpc_user.text().strip(),
            "password": self.entry_rpc_pass.text(),
            "remember": self.check_rpc_remember.isChecked(),
        })

    def _on_shell_send(self) -> None:
        text = self.entry_shell_in.text()
        if text.strip():
            self._emit("shell-send", text)
            self.entry_shell_in.clear()

    def _on_devmode_toggled(self, on: bool) -> None:
        self._emit("devmode", bool(on))

    def _on_test_run(self) -> None:
        self._emit("test-run", {
            "module": self.entry_test_module.text().strip(),
            "db": self.entry_test_db.text().strip(),
        })

    def shell_append(self, text: str) -> None:
        self.shell_output.append(text)

    def shell_set_status(self, text: str) -> None:
        self.lbl_shell_status.setText(text)

    def set_devmode_state(self, on: bool, note: str = "") -> None:
        blocked = self.check_devmode.signalsBlocked()
        self.check_devmode.blockSignals(True)
        self.check_devmode.setChecked(on)
        self.check_devmode.blockSignals(blocked)
        self.lbl_devmode.setText(note)

    def _emit(self, action: str, payload) -> None:
        if self._instance_id is not None:
            self.actionRequested.emit(action, self._instance_id, payload)
