"""Modules page (PSQ-5.2): faithful port of the GTK Modules tab.

Search + state filter + Refresh; multi-select SelectionList (dogfood #2:
Install multi-pick is exactly the checkbox case); Install/Update act on
checked modules; Uninstall/Update-Code/Dependencies act on the current
row. Emits actionRequested(action, instance_id, payload).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.icons import style_button  # noqa: E402

from odoo_vite.ui_qt.widgets.selection_list import SelectionList

STATE_FILTERS = ["All", "Installed", "Upgradeable", "Installable"]


def _ver_tuple(version_str: str) -> tuple:
    """Local copy of module_manager._ver_tuple (thin adapter: version
    comparison for the state filter; core/ owns the real logic)."""
    parts = []
    for chunk in str(version_str or "").replace("-", ".").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def state_category(mod: dict, diff: dict | None = None) -> str:
    """GTK _mod_state_category parity (pure, unit-tested)."""
    state = (mod.get("state") or "").strip()
    if state == "installed":
        installed = _ver_tuple(mod.get("installed_version") or "")
        available = _ver_tuple(mod.get("available_version") or "")
        latest = _ver_tuple(mod.get("latest_version") or "")
        if available and available > installed:
            return "Upgradeable"
        if latest and latest > installed:
            return "Upgradeable"
        return "Installed"
    if state in ("to install", "to upgrade"):
        return "Upgradeable"
    return "Installable"


class ModulesPage(QWidget):
    actionRequested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._modules_cache: list = []
        self._diff_cache: dict = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        head = QHBoxLayout()
        title = QLabel("Modules")
        title.setProperty("class", "heading")
        head.addWidget(title, 1)
        self.lbl_mod_db = QLabel()
        self.lbl_mod_db.setProperty("class", "dim")
        head.addWidget(self.lbl_mod_db)
        layout.addLayout(head)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText("Filter by name or summary…")
        self.entry_search.setClearButtonEnabled(True)
        self.entry_search.textChanged.connect(lambda _t: self._render_rows())
        filter_row.addWidget(self.entry_search, 1)
        self.state_filter = QComboBox()
        self.state_filter.addItems(STATE_FILTERS)
        self.state_filter.currentIndexChanged.connect(
            lambda _i: self._render_rows())
        filter_row.addWidget(self.state_filter)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(
            lambda: self._emit("mod-refresh", None))
        filter_row.addWidget(self.btn_refresh)
        layout.addLayout(filter_row)

        self.mod_list = SelectionList(multi=True, searchable=False,
                                      max_visible_rows=20, parent=self)
        # Picks survive filtering: the checked set lives here, not in the
        # (rebuilt) model — re-applied on every render.
        self._checked: set = set()
        self.mod_list.checkedChanged.connect(self._on_checks_changed)
        self.mod_list.selectionChanged.connect(self._sync_buttons)
        self.mod_list.selectionChanged.connect(self._sync_buttons)
        self.mod_list.view.selectionModel().currentChanged.connect(
            lambda *_: self._sync_buttons())
        layout.addWidget(self.mod_list, 1)
        self.lbl_modules_empty = QLabel(
            "No modules loaded — select an instance with a primary "
            "database, then press Refresh.")
        self.lbl_modules_empty.setProperty("class", "dim")
        self.lbl_modules_empty.setWordWrap(True)
        layout.addWidget(self.lbl_modules_empty)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_install = style_button(
            QPushButton("Install Checked"), "run", primary=True)
        self.btn_install.clicked.connect(self._on_install)
        btn_row.addWidget(self.btn_install)
        self.btn_update = QPushButton("Update Checked")
        self.btn_update.clicked.connect(self._on_update)
        btn_row.addWidget(self.btn_update)
        self.btn_uninstall = style_button(QPushButton("Uninstall"), "uninstall")
        self.btn_uninstall.setProperty("role", "destructive")
        self.btn_uninstall.setToolTip("Uninstall the current row's module")
        self.btn_uninstall.clicked.connect(self._on_uninstall)
        btn_row.addWidget(self.btn_uninstall)
        self.btn_update_code = QPushButton("Update Code…")
        self.btn_update_code.setToolTip(
            "git pull community + pip install + -u (instance must be stopped)")
        self.btn_update_code.clicked.connect(
            lambda: self._emit("mod-update-code", None))
        btn_row.addWidget(self.btn_update_code)
        self.btn_deps = QPushButton("Dependencies…")
        self.btn_deps.clicked.connect(self._on_deps)
        btn_row.addWidget(self.btn_deps)
        self.btn_scaffold = style_button(QPushButton("New Module…"), "new")
        self.btn_scaffold.clicked.connect(
            lambda: self._emit("mod-scaffold", None))
        btn_row.addWidget(self.btn_scaffold)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        self._sync_buttons()

    # ------------------------------------------------------------------ API

    def show_instance(self, instance, db_name: str = "") -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
            db_name = db_name or instance.get("primary_db", "") or ""
        else:
            self._instance_id = instance.id
            db_name = db_name or instance.primary_db or ""
        self.lbl_mod_db.setText(f"on {db_name}" if db_name else "")
        self._checked = set()
        self._modules_cache = []

    def set_modules(self, modules: list, diff: dict,
                    error: str = "") -> None:
        self._modules_cache = modules or []
        self._diff_cache = diff or {}
        if error:
            self.lbl_mod_db.setText(error)
        self.lbl_modules_empty.setVisible(len(self._modules_cache) == 0)
        self._render_rows()

    # -------------------------------------------------------------- internals

    def _current_row_name(self):
        return self.mod_list.current_id()

    def _render_rows(self) -> None:
        needle = self.entry_search.text().strip().lower()
        filt = self.state_filter.currentText()
        rows = []
        for mod in self._modules_cache:
            name = mod.get("name", "")
            if needle and needle not in name.lower() and needle not in str(
                    mod.get("summary", "")).lower():
                continue
            if filt != "All" and state_category(mod) != filt:
                continue
            badge = state_category(mod)
            info = (self._diff_cache or {}).get(name)
            if info and info.get("status") not in (None, "in-sync"):
                badge += f"  ⚠ {info.get('note', '')}"
            rows.append({"id": name, "title": name, "badge": badge,
                         "checked": name in self._checked})
        self.mod_list.set_items(rows)
        self._sync_buttons()

    def _on_checks_changed(self) -> None:
        self._checked = set(self.mod_list.checked_ids())
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        checked = self.mod_list.checked_ids()
        current = self._current_row_name()
        self.btn_install.setEnabled(bool(checked))
        self.btn_update.setEnabled(bool(checked))
        self.btn_uninstall.setEnabled(current is not None)
        self.btn_deps.setEnabled(current is not None)

    def _on_install(self) -> None:
        names = self.mod_list.checked_ids()
        if names:
            self._emit("mod-install", names)

    def _on_update(self) -> None:
        names = self.mod_list.checked_ids()
        if names:
            self._emit("mod-update", names)

    def _on_uninstall(self) -> None:
        name = self._current_row_name()
        if name:
            self._emit("mod-uninstall", name)

    def _on_deps(self) -> None:
        name = self._current_row_name()
        if name:
            self._emit("mod-deps", name)

    def _emit(self, action: str, payload) -> None:
        if self._instance_id is not None:
            self.actionRequested.emit(action, self._instance_id, payload)
