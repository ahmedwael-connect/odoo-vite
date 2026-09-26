"""Configuration flows (PSQ-6.4): faithful port of GTK configuration.py.

Conf/meta writes reuse conf_manager/update_instance exactly as-is.
The Addon Path Manager is dogfood #3 for SelectionList: Enable/Disable
is the component's real CheckState (the F2.1 saga ends here), paths show
full text via tooltip, and the dialog sizes to content, resizable, with
no fixed-width truncation (F2.2 lesson built in, not bolted on).
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.core import addon_paths, conf_manager
from odoo_vite.core.registry import get_instance, update_instance
from odoo_vite.core.result import Result
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm
from odoo_vite.ui_qt.widgets.selection_list import SelectionList
from odoo_vite.ui_qt.workers import run_in_background


class ConfigurationFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    refreshRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------ conf writes

    def save(self, instance_id: str, changes: dict) -> None:
        self.message.emit("Saving…")

        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            return conf_manager.update_conf_keys(inst.conf_path,
                                                 changes or {})

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def restore(self, instance_id: str) -> None:
        self.message.emit("Restoring…")

        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            return conf_manager.restore_conf_backup(inst.conf_path)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def regenerate(self, instance_id: str) -> None:
        from PySide6.QtWidgets import QWidget

        inst = get_instance(instance_id)
        if inst is None:
            return
        parent = self.parent()
        if not ask_confirm(
                parent if isinstance(parent, QWidget) else None,
                "Regenerate odoo.conf from registry?",
                "Rebuilds [options] from registry fields — manual edits "
                "to the file will be overwritten.",
                "Regenerate", destructive=True):
            return
        self.message.emit("Regenerating…")

        def _work():
            return conf_manager.regenerate_conf(inst)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def meta_save(self, instance_id: str, meta: dict) -> None:
        self.message.emit("Saving metadata…")

        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance disappeared")
            # v2 parity: workers>0 pre-check runs before ANY write.
            pre = conf_manager.check_workers_prereqs(
                int(meta.get("workers", 0) or 0), inst.conf_path)
            if not pre.ok:
                return Result.failure(pre.message)
            res = update_instance(
                instance_id,
                description=meta.get("description", ""),
                workers=int(meta.get("workers", 0) or 0),
                log_level=meta.get("log_level", "info"),
                python_binary=meta.get("python_binary", ""))
            if not res.ok:
                return Result.failure(res.message)
            conf_res = conf_manager.update_conf_keys(inst.conf_path, {
                "workers": str(int(meta.get("workers", 0) or 0)),
                "log_level": meta.get("log_level", "info"),
            })
            if not conf_res.ok:
                return Result.failure(
                    "Metadata saved, but conf write failed: "
                    + conf_res.message)
            return Result.success(
                message="Metadata saved (registry + odoo.conf)")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ addon manager

    def addons_manage(self, parent_widget, instance_id: str) -> None:
        """Addon Path Manager on SelectionList (F2.1/F2.2 lessons applied)."""
        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            entries = addon_paths.get_addons_state(inst)
        except Exception as exc:
            self.message.emit(f"Cannot load addon paths: {exc}")
            return
        entries = [dict(e) for e in entries]

        dlg = QDialog(parent_widget if isinstance(parent_widget, QWidget)
                      else None)
        dlg.setWindowTitle(f"Addon paths — {inst.name}")
        # Sizes to content, resizable: minimum floor, no maximum cap, and
        # the list (not the dialog) scrolls past max_visible_rows.
        dlg.setMinimumSize(640, 420)
        dlg.resize(700, 480)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(QLabel(
            "Order matters (first match wins in Odoo). Unchecked paths "
            "stay listed but are excluded from addons_path."))
        picker = SelectionList(multi=True, parent=dlg)
        layout.addWidget(picker, 1)

        def _sync_back() -> None:
            """Fold current checkbox states back into entries."""
            checked = set(picker.checked_ids())
            for entry in entries:
                entry["enabled"] = entry["path"] in checked

        def _rebuild() -> None:
            picker.set_items([
                {"id": e["path"], "title": e["path"],
                 "badge": "" if e.get("enabled", True) else "disabled",
                 "checked": bool(e.get("enabled", True))}
                for e in entries])

        row_btns = QHBoxLayout()
        row_btns.setSpacing(8)
        btn_edit = QPushButton("Edit")
        btn_edit.setToolTip("Change this path string")
        btn_edit.clicked.connect(lambda: _edit_current())
        row_btns.addWidget(btn_edit)
        btn_up = QPushButton("↑")
        btn_up.clicked.connect(lambda: _move_current(-1))
        row_btns.addWidget(btn_up)
        btn_down = QPushButton("↓")
        btn_down.clicked.connect(lambda: _move_current(1))
        row_btns.addWidget(btn_down)
        btn_rm = QPushButton("Remove")
        btn_rm.clicked.connect(lambda: _remove_current())
        row_btns.addWidget(btn_rm)
        row_btns.addStretch(1)
        layout.addLayout(row_btns)

        def _current_path():
            return picker.current_id()

        def _edit_current() -> None:
            old = _current_path()
            if not old:
                return
            new, ok = QInputDialog.getText(
                dlg, "Edit addon path", "Path:", text=old)
            new = (new or "").strip()
            if ok and new and new != old:
                _sync_back()
                for entry in entries:
                    if entry["path"] == old:
                        entry["path"] = new
                _rebuild()

        def _move_current(delta: int) -> None:
            cur = _current_path()
            if not cur:
                return
            _sync_back()
            idx = next((i for i, e in enumerate(entries)
                        if e["path"] == cur), None)
            if idx is None:
                return
            other = idx + delta
            if 0 <= other < len(entries):
                entries[idx], entries[other] = entries[other], entries[idx]
                _rebuild()
                picker.select_id(cur)

        def _remove_current() -> None:
            cur = _current_path()
            if not cur:
                return
            _sync_back()
            entries[:] = [e for e in entries if e["path"] != cur]
            _rebuild()

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        path_lbl = QLabel("—")
        path_lbl.setProperty("class", "dim")
        add_row.addWidget(path_lbl, 1)
        picked: dict = {}

        def _browse() -> None:
            folder = QFileDialog.getExistingDirectory(
                dlg, "Select addons folder")
            if folder:
                picked["path"] = folder
                path_lbl.setText(folder)
                if not addon_paths.looks_like_addons_folder(folder):
                    self.message.emit(
                        "Warning: no subfolder with __manifest__.py found "
                        "— you may still add it")

        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(_browse)
        add_row.addWidget(btn_browse)
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(lambda: _add_picked())
        add_row.addWidget(btn_add)
        layout.addLayout(add_row)

        def _add_picked() -> None:
            path = (picked.get("path") or "").strip()
            if not path:
                self.message.emit("Browse for a folder first")
                return
            if path in [e["path"] for e in entries]:
                self.message.emit("Already in the list")
                return
            _sync_back()
            entries.append({"path": path, "enabled": True})
            picked.clear()
            path_lbl.setText("—")
            _rebuild()

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_apply = QPushButton("Apply changes")
        btn_apply.clicked.connect(lambda: _apply())
        bottom.addWidget(btn_apply)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        bottom.addWidget(btn_cancel)
        layout.addLayout(bottom)

        def _apply() -> None:
            _sync_back()

            def _work():
                return addon_paths.apply_addons_state(instance_id, entries)

            def _done(ok: bool, message: str, _data: dict) -> None:
                self.message.emit(message)
                self.refreshRequested.emit()
                if ok:
                    dlg.accept()

            run_in_background(self, _work, _done)

        _rebuild()
        dlg.exec()
