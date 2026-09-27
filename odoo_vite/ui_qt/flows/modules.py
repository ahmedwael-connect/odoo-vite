"""Module flows (PSQ-5.3): faithful port of GTK module_ops.py.

Long ops (install/update/uninstall/update-code) show the exact command
for confirmation (GTK _confirm_command parity) then stream into a
ProgressDialog. Core imports live at module top — never first-import a
module from inside a worker thread (shiboken import-hook abort, PSQ-4).
"""

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from odoo_vite.core import db_backup, module_manager  # noqa: E402
from odoo_vite.core.proc import run_streaming  # noqa: E402
from odoo_vite.core.registry import get_db_password, get_instance  # noqa: E402
from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm  # noqa: E402
from odoo_vite.ui_qt.widgets.progress_dialog import ProgressDialog  # noqa: E402
from odoo_vite.ui_qt.widgets.selection_list import SelectionList  # noqa: E402
from odoo_vite.ui_qt.workers import run_in_background  # noqa: E402


class ModuleFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    refreshRequested = Signal()
    modulesReady = Signal(str, list, dict, str)  # (id, modules, diff, error)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------ helpers

    def _instance_db(self, instance_id: str):
        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit("Instance disappeared")
            return None, ""
        db_name = (inst.primary_db or "").strip()
        if not db_name:
            self.message.emit("No primary database set — pick one first.")
            return None, ""
        return inst, db_name

    def _confirm_command(self, heading: str, command: str,
                         confirm_label: str) -> bool:
        return ask_confirm(self._parent_widget(),
                           heading, f"Runs:\n{command}", confirm_label,
                           destructive="Uninstall" in confirm_label
                           or "Drop" in confirm_label)

    def _run_with_progress(self, instance_id: str, title: str,
                           op, *args, **kwargs) -> None:
        dlg = ProgressDialog(self._parent_widget(), title)
        dlg.show()

        def _work():
            try:
                return op(*args, progress_cb=dlg.request_append.emit,
                          cancel=dlg.cancel_event.is_set, **kwargs)
            except TypeError:
                return op(*args, **kwargs)

        def _done(ok: bool, message: str, _data: dict) -> None:
            dlg.request_done.emit(ok, message)
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ refresh

    def refresh_modules(self, instance_id: str) -> None:
        def _work():
            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance not found")
            db_name = (inst.primary_db or "").strip()
            if not db_name:
                return Result.failure("No primary database set")
            mods = module_manager.list_modules(inst, db_name)
            diff = {}
            if mods.ok:
                dres = module_manager.diff_modules(inst, db_name)
                if dres.ok:
                    diff = {r["name"]: r
                            for r in dres.data.get("diff", [])}
            return Result(ok=mods.ok,
                          message="" if mods.ok else mods.message,
                          data={"modules": list(mods.data.get("modules", []))
                                if mods.ok else [],
                                "diff": diff})

        def _done(ok: bool, message: str, data: dict) -> None:
            self.modulesReady.emit(instance_id, data.get("modules", []),
                                   data.get("diff", {}),
                                   "" if ok else message)

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ ops

    def _odoo_cmd(self, inst, db_name: str, flag: str, names: list) -> list:
        return [f"{inst.venv_path}/bin/python",
                f"{inst.community_path}/odoo-bin",
                "-c", inst.conf_path, "-d", db_name,
                flag, ",".join(names), "--stop-after-init"]

    def install(self, instance_id: str, names: list) -> None:
        names = [n for n in (names or []) if n]
        if not names:
            self.message.emit("Select installable modules first")
            return
        got = self._instance_db(instance_id)
        if got[0] is None:
            return
        inst, db_name = got
        cmd = self._odoo_cmd(inst, db_name, "-i", names)
        if not self._confirm_command(
                f"Install {', '.join(names)} into '{db_name}'?",
                " ".join(cmd), "Install"):
            return
        self._run_with_progress(
            instance_id, f"Install {', '.join(names)}",
            module_manager.install_modules, inst, db_name, names)

    def update(self, instance_id: str, names: list) -> None:
        names = [n for n in (names or []) if n]
        if not names:
            self.message.emit("Select modules to update first")
            return
        got = self._instance_db(instance_id)
        if got[0] is None:
            return
        inst, db_name = got
        cmd = self._odoo_cmd(inst, db_name, "-u", names)
        if not self._confirm_command(
                f"Update {', '.join(names)} in '{db_name}'?",
                " ".join(cmd), "Update"):
            return

        def _op(*args, progress_cb=None, **kwargs):
            res = run_streaming(cmd, progress_cb=progress_cb, timeout=3600)
            if not res.ok:
                tail = "\n".join((res.data or {}).get("lines", [])[-10:])
                return Result.failure(
                    f"Update failed: {res.message}"
                    + (f"\n--- tail ---\n{tail}" if tail else ""))
            return Result.success(
                message=f"Updated {', '.join(names)}")

        self._run_with_progress(
            instance_id, f"Update {', '.join(names)}", _op)

    def uninstall(self, instance_id: str, name: str) -> None:
        if not name:
            return
        got = self._instance_db(instance_id)
        if got[0] is None:
            return
        inst, db_name = got
        cmd = (f"{inst.venv_path}/bin/python "
               f"{inst.community_path}/odoo-bin -c {inst.conf_path} "
               f"-d {db_name} --uninstall {name} --stop-after-init")
        if not self._confirm_command(f"Uninstall {name} from '{db_name}'?",
                                     cmd, "Uninstall"):
            return
        self._run_with_progress(
            instance_id, f"Uninstall {name}",
            module_manager.uninstall_modules, inst, db_name, [name])

    def update_code(self, instance_id: str) -> None:
        got = self._instance_db(instance_id)
        if got[0] is None:
            return
        inst, db_name = got
        if (inst.status or "") == "running":
            self.message.emit(
                "Stop the instance first — code changes under a live "
                "server would half-apply")
            return
        mods = list(inst.auto_update_modules or [])
        if not mods:
            self.message.emit(
                "No auto-update modules configured — nothing to update")
            return
        if not self._confirm_command(
                "Update code and modules?",
                "Riskier than Install: git pull community, pip install, "
                f"then -u {','.join(mods)} (instance stays stopped; "
                "restart it yourself afterwards)",
                "Update code"):
            return
        if ask_confirm(self._parent_widget(),
                       "Back up the primary database first?",
                       "Recommended — automatic pre-update backup.",
                       "Back up first"):
            self._backup_before_update(instance_id, inst, db_name)
        self._run_with_progress(
            instance_id, "Update code + modules",
            module_manager.update_code, inst, mods)

    def _parent_widget(self):
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def _backup_before_update(self, instance_id: str, inst,
                              db_name: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = str(Path(inst.path)
                   / f"pre-update-{db_name}-{stamp}.dump")
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        res = db_backup.backup_database(
            db_name, dest, db_user=inst.db_user, db_password=pw,
            instance_id=inst.id, instance_name=inst.name)
        self.message.emit(res.message)

    def show_deps(self, instance_id: str, name: str) -> None:
        got = self._instance_db(instance_id)
        if got[0] is None:
            return
        inst, db_name = got
        res = module_manager.get_dependency_graph(inst, db_name)
        if not res.ok:
            self.message.emit(res.message)
            return
        graph = res.data or {}
        depends = sorted(graph.get("depends", {}).get(name, []))
        required_by = sorted(graph.get("required_by", {}).get(name, []))
        parent = self.parent()
        dlg = QDialog(parent if isinstance(parent, QWidget) else None)
        dlg.setWindowTitle(f"Dependencies — {name}")
        dlg.setMinimumWidth(560)
        layout = QVBoxLayout(dlg)
        panes = QHBoxLayout()
        for title, names in (("Depends on", depends),
                             ("Required by", required_by)):
            pane = QVBoxLayout()
            head = QLabel(title)
            head.setProperty("class", "heading")
            pane.addWidget(head)
            lst = SelectionList(multi=False, parent=dlg)
            lst.set_items([{"id": n, "title": n} for n in names])
            pane.addWidget(lst)
            panes.addLayout(pane)
        layout.addLayout(panes)
        dlg.exec()
