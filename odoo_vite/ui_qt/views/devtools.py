"""Dev Tools page (PSQ-8.2, Part B redesign): grouped sections.

Each section is a QGroupBox (single approach everywhere, per Part B) —
never a flat stack of labels. Empty result areas show explicit empty
states, never blank voids. Emits actionRequested; flows execute.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
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

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402

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
        layout.setSpacing(12)
        layout.setContentsMargins(16, 12, 16, 16)
        scrolled.setWidget(body)
        outer.addWidget(scrolled)

        # ---------------------------------------------------------- RPC
        rpc_box, rpc = self._section(layout, "RPC connection")
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
        self.btn_rpc_connect = style_button(
            QPushButton("Connect"), "connect", primary=True)
        self.btn_rpc_connect.clicked.connect(self._on_rpc_connect)
        rpc_row.addWidget(self.btn_rpc_connect)
        rpc.addLayout(rpc_row)
        self.lbl_rpc_status = QLabel("Not connected.")
        self.lbl_rpc_status.setProperty("class", "dim")
        self.lbl_rpc_status.setWordWrap(True)
        rpc.addWidget(self.lbl_rpc_status)

        # ---------------------------------------------------------- models
        _models_box, models = self._section(layout, "Model Inspector")
        self.models_list = SelectionList(multi=False, parent=self)
        models.addWidget(self.models_list)
        self.lbl_models_empty = QLabel(
            "Connect to an instance to inspect models.")
        self.lbl_models_empty.setProperty("class", "dim")
        models.addWidget(self.lbl_models_empty)
        self.meta_list = QListWidget()
        self.meta_list.setMaximumHeight(150)
        models.addWidget(self.meta_list)
        self.lbl_model_meta = QLabel()
        self.lbl_model_meta.setProperty("class", "dim")
        models.addWidget(self.lbl_model_meta)
        self.models_list.selectionChanged.connect(
            self._sync_models_empty)

        # ---------------------------------------------------------- records
        _rec_box, rec = self._section(layout, "Record Browser")
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
        rec.addLayout(dom_row)
        self.records_list = SelectionList(multi=False, parent=self)
        rec.addWidget(self.records_list)
        self.lbl_records_empty = QLabel("No query yet — search above.")
        self.lbl_records_empty.setProperty("class", "dim")
        rec.addWidget(self.lbl_records_empty)
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
        rec.addLayout(rec_nav)
        rec_ops = QHBoxLayout()
        rec_ops.setSpacing(8)
        self.btn_rec_new = style_button(QPushButton("New…"), "new")
        self.btn_rec_new.clicked.connect(
            lambda: self._emit("rec-new", None))
        rec_ops.addWidget(self.btn_rec_new)
        self.btn_rec_edit = QPushButton("Edit…")
        self.btn_rec_edit.clicked.connect(
            lambda: self._emit("rec-edit", None))
        rec_ops.addWidget(self.btn_rec_edit)
        self.btn_rec_delete = style_button(QPushButton("Delete…"), "delete")
        self.btn_rec_delete.setProperty("role", "destructive")
        self.btn_rec_delete.clicked.connect(
            lambda: self._emit("rec-delete", None))
        rec_ops.addWidget(self.btn_rec_delete)
        rec_ops.addStretch(1)
        rec.addLayout(rec_ops)

        # ---------------------------------------------------------- cron
        _cron_box, cron = self._section(layout, "Cron Jobs")
        cron_row = QHBoxLayout()
        cron_row.setSpacing(8)
        self.btn_cron_refresh = QPushButton("Refresh")
        self.btn_cron_refresh.clicked.connect(
            lambda: self._emit("cron-refresh", None))
        cron_row.addWidget(self.btn_cron_refresh)
        cron_row.addStretch(1)
        cron.addLayout(cron_row)
        self.cron_list = QListWidget()
        self.cron_list.setMaximumHeight(150)
        cron.addWidget(self.cron_list)
        self.lbl_cron_empty = QLabel("No cron jobs loaded — press Refresh.")
        self.lbl_cron_empty.setProperty("class", "dim")
        cron.addWidget(self.lbl_cron_empty)

        # ---------------------------------------------------------- launch/editors
        _tool_box, tools = self._section(layout, "Launch & editors")
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
        tools.addLayout(tool_row)

        # ---------------------------------------------------------- shell
        _shell_box, shell = self._section(layout, "Odoo Shell (PTY REPL)")
        shell_row = QHBoxLayout()
        shell_row.setSpacing(8)
        self.btn_shell_start = style_button(QPushButton("Start shell"), "run")
        self.btn_shell_start.clicked.connect(
            lambda: self._emit("shell-start", None))
        shell_row.addWidget(self.btn_shell_start)
        self.btn_shell_stop = style_button(QPushButton("Stop"), "stop")
        self.btn_shell_stop.clicked.connect(
            lambda: self._emit("shell-stop", None))
        shell_row.addWidget(self.btn_shell_stop)
        self.lbl_shell_status = QLabel("Shell not running.")
        self.lbl_shell_status.setProperty("class", "dim")
        shell_row.addWidget(self.lbl_shell_status, 1)
        shell.addLayout(shell_row)
        self.shell_output = QTextEdit()
        self.shell_output.setReadOnly(True)
        self.shell_output.setFontFamily("monospace")
        self.shell_output.setMinimumHeight(200)
        self.shell_output.setMaximumHeight(320)
        shell.addWidget(self.shell_output)
        self.entry_shell_in = QLineEdit()
        self.entry_shell_in.setPlaceholderText(
            "Type Python to run in the shell… (Enter sends)")
        self.entry_shell_in.returnPressed.connect(self._on_shell_send)
        shell.addWidget(self.entry_shell_in)

        # ---------------------------------------------------------- devmode
        _dev_box, dev = self._section(layout, "Dev Mode Watch")
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        self.check_devmode = QCheckBox("Watch addon files, auto-restart")
        self.check_devmode.toggled.connect(self._on_devmode_toggled)
        dev_row.addWidget(self.check_devmode)
        self.lbl_devmode = QLabel()
        self.lbl_devmode.setProperty("class", "dim")
        dev_row.addWidget(self.lbl_devmode, 1)
        dev.addLayout(dev_row)

        # ---------------------------------------------------------- tests
        _test_box, test = self._section(layout, "Module tests")
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        self.entry_test_module = QLineEdit()
        self.entry_test_module.setPlaceholderText("module name")
        test_row.addWidget(self.entry_test_module, 1)
        self.entry_test_db = QLineEdit()
        self.entry_test_db.setPlaceholderText("test database (never primary!)")
        test_row.addWidget(self.entry_test_db, 1)
        self.btn_test_run = style_button(
            QPushButton("Run tests"), "run", primary=True)
        self.btn_test_run.clicked.connect(self._on_test_run)
        test_row.addWidget(self.btn_test_run)
        test.addLayout(test_row)
        layout.addStretch(1)
        self._sync_models_empty()
        self._sync_cron_empty()

    # ------------------------------------------------------------------ API

    @staticmethod
    def _section(parent_layout, title: str):
        """One bordered group per section — the single Part B approach."""
        box = QGroupBox(title)
        body = QVBoxLayout(box)
        body.setSpacing(8)
        body.setContentsMargins(12, 12, 12, 12)
        parent_layout.addWidget(box)
        return box, body

    def show_instance(self, instance) -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
        else:
            self._instance_id = instance.id

    # -------------------------------------------------------------- internals

    def _sync_models_empty(self) -> None:
        self.lbl_models_empty.setVisible(
            self.models_list.visible_count() == 0)

    def _sync_cron_empty(self) -> None:
        self.lbl_cron_empty.setVisible(self.cron_list.count() == 0)

    def set_crons(self, crons: list) -> None:
        self.cron_list.clear()
        for cron in crons:
            self.cron_list.addItem(
                f"{cron.get('name', '?')} — next: {cron.get('nextcall', '?')} "
                f"({'active' if cron.get('active') else 'paused'})")
        self._sync_cron_empty()

    def set_records(self, rows: list) -> None:
        self.records_list.set_items(rows)
        self.lbl_records_empty.setVisible(len(rows) == 0)

    def set_models(self, rows: list) -> None:
        self.models_list.set_items(rows)
        self._sync_models_empty()

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

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        for btn in (self.btn_rpc_connect, self.btn_rec_search,
                    self.btn_rec_prev, self.btn_rec_next, self.btn_rec_new,
                    self.btn_rec_edit, self.btn_rec_delete,
                    self.btn_cron_refresh, self.btn_launch_json,
                    self.btn_open_code, self.btn_open_cursor,
                    self.btn_shell_start, self.btn_shell_stop,
                    self.btn_test_run):
            try:
                btn.setEnabled(enabled)
            except Exception:
                pass
