"""Backup schedule flows (BKQ port): faithful port of GTK backup_schedules.

Reuses core/backup_scheduler.py exactly as-is (no new core logic).
Db picker is SelectionList multi-select pre-checked from tracked DBs —
the gray-circle picker's replacement, built right the first time.
"""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from datetime import datetime as _dt
from pathlib import Path as _Path

from odoo_vite.core import backup_scheduler, db_backup
from odoo_vite.core.registry import get_db_password, get_instance
from odoo_vite.core.result import Result
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm, ask_confirm_typed
from odoo_vite.ui_qt.widgets.selection_list import SelectionList
from odoo_vite.ui_qt.workers import run_in_background

PRESETS = [
    ("Hourly", "0 * * * *"),
    ("Daily 02:00", "0 2 * * *"),
    ("Weekly Sun 02:00", "0 2 * * 0"),
    ("Custom…", ""),
]


class BackupSchedulesFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    refreshRequested = Signal()
    schedulesReady = Signal(str, list, dict)  # (id, schedules, timer status)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------ refresh

    def refresh_schedules(self, instance_id: str) -> None:
        def _work():

            try:
                scheds = backup_scheduler.list_schedules(instance_id)
                status = backup_scheduler.timer_status()
            except Exception as exc:
                return Result.failure(str(exc))
            return Result(ok=True, message="",
                          data={"schedules": scheds, "status": status})

        def _done(ok: bool, _message: str, data: dict) -> None:
            if ok:
                self.schedulesReady.emit(
                    instance_id, data.get("schedules", []),
                    data.get("status", {}))

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ add

    def sched_add_dialog(self, parent_widget, instance_id: str) -> None:
        self._schedule_dialog(parent_widget, instance_id, edit_sid=None)

    def sched_edit_dialog(self, parent_widget, instance_id: str,
                          schedule_id: str) -> None:
        self._schedule_dialog(parent_widget, instance_id,
                              edit_sid=schedule_id)

    def _schedule_dialog(self, parent_widget, instance_id: str,
                         edit_sid: str | None) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        existing = None
        if edit_sid is not None:
            existing = backup_scheduler.get_schedule(edit_sid)
            if existing is None:
                self.message.emit("Schedule not found")
                return
        names = list(dict.fromkeys(
            ([inst.primary_db] if inst.primary_db else [])
            + (inst.tracked_dbs or [])))
        if not names:
            self.message.emit("Track a database first")
            return
        dlg = QDialog(parent_widget if isinstance(parent_widget, QWidget)
                      else None)
        dlg.setWindowTitle(
            f"Edit schedule — {inst.name}" if existing is not None
            else f"Scheduled backups — {inst.name}")
        dlg.setMinimumSize(560, 520)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(QLabel("Databases to back up:"))
        picker = SelectionList(multi=True, parent=dlg)
        prechecked = (set(existing.databases) if existing is not None
                      else set(names))
        picker.set_items([{"id": n, "title": n, "checked": n in prechecked}
                          for n in names])
        layout.addWidget(picker, 1)
        form = QFormLayout()
        form.setSpacing(8)
        cron_row = QHBoxLayout()
        cron_row.setSpacing(8)
        drop = QComboBox()
        drop.addItems([label for label, _ in PRESETS])
        cron_entry = QLineEdit()
        cron_entry.setToolTip(
            "cron expression: minute hour day month weekday")
        cron_row.addWidget(cron_entry, 1)
        drop.currentIndexChanged.connect(
            lambda idx: cron_entry.setText(PRESETS[idx][1])
            if PRESETS[idx][1] else None)
        # Set values AFTER wiring: the index change must not clobber a
        # custom expression (verified live: it blanked the field).
        start_cron = (existing.cron if existing is not None else "0 2 * * *")
        try:
            drop.setCurrentIndex([expr for _, expr in PRESETS].index(
                start_cron))
        except ValueError:
            drop.setCurrentIndex(3)  # Custom…
            cron_entry.setText(start_cron)
        form.addRow("Schedule:", cron_row)
        spin_n = QSpinBox()
        spin_n.setRange(1, 365)
        spin_n.setValue(existing.retention_n if existing is not None else 7)
        spin_n.setToolTip("Keep last N backups")
        form.addRow("Keep last:", spin_n)
        spin_days = QSpinBox()
        spin_days.setRange(0, 3650)
        spin_days.setValue(
            existing.retention_days if existing is not None else 0)
        spin_days.setToolTip("Keep days (0 = off)")
        form.addRow("Keep days (0 = off):", spin_days)
        layout.addLayout(form)
        hint = QLabel(
            "Runs even when Odoo Vite is closed (systemd timer, checked "
            "every minute). Backups land under "
            "~/.local/share/odoo-vite/backups/. Dumps use pg_dump -Fc "
            "(compressed custom format). Retention prunes: only the newest "
            "“Keep last” dumps within “Keep days” survive — older files are "
            "deleted.")
        hint.setProperty("class", "dim")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText(
            "Save changes" if existing is not None else "Save schedule")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = picker.checked_ids()
        if not chosen:
            self.message.emit("Pick at least one database")
            return
        if existing is not None:
            self.update_schedule(
                instance_id, existing.id, chosen,
                cron_entry.text().strip(), int(spin_n.value()),
                int(spin_days.value()))
        else:
            self.create_schedule(
                instance_id, chosen, cron_entry.text().strip(),
                int(spin_n.value()), int(spin_days.value()))

    def create_schedule(self, instance_id: str, databases: list,
                        cron: str, retention_n: int = 7,
                        retention_days: int = 0) -> None:
        """Shared by the dialog (and tests): validate, save, install timer."""

        def _work():
            res = backup_scheduler.create_schedule(
                instance_id, databases, cron,
                retention_n=retention_n, retention_days=retention_days)
            if not res.ok:
                return res
            timer = backup_scheduler.install_timer()
            if not timer.ok:
                return Result(ok=True,
                              message=f"Schedule saved; timer issue: "
                                      f"{timer.message}",
                              data=res.data)
            return res

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    def update_schedule(self, instance_id: str, schedule_id: str,
                        databases: list, cron: str, retention_n: int,
                        retention_days: int) -> None:
        """Edit path: UPDATE in place (id + run history survive). No
        timer reinstall — the per-minute timer is schedule-agnostic."""

        def _work():
            return backup_scheduler.update_schedule(
                schedule_id, databases=databases, cron=cron,
                retention_n=retention_n, retention_days=retention_days)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ mutations

    def sched_delete(self, parent_widget, instance_id: str,
                     schedule_id: str) -> None:
        if not ask_confirm(
                parent_widget if isinstance(parent_widget, QWidget) else None,
                "Delete this backup schedule?",
                "Scheduled runs stop. Existing backup files are kept.",
                "Delete schedule", destructive=True):
            return

        def _work():
            return backup_scheduler.delete_schedule(schedule_id)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    def sched_toggle(self, instance_id: str, schedule_id: str) -> None:
        sched = backup_scheduler.get_schedule(schedule_id)
        if sched is None:
            self.message.emit("Schedule not found")
            return

        def _work():
            return backup_scheduler.set_enabled(schedule_id,
                                                not sched.enabled)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    def sched_run_now(self, instance_id: str, schedule_id: str) -> None:
        self.message.emit("Running backup now…")

        def _work():
            return backup_scheduler.run_schedule(schedule_id)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ file list

    def backups_browse_dialog(self, parent_widget,
                              instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        files = backup_scheduler.list_backup_files(inst.name)
        dlg = QDialog(parent_widget if isinstance(parent_widget, QWidget)
                      else None)
        dlg.setWindowTitle("Backup files")
        dlg.setMinimumSize(620, 420)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(QLabel(
            "Scheduled backups for this instance (newest first). "
            "Manual backups saved elsewhere are not listed here."))
        file_list = QListWidget()
        for entry in files:
            meta = entry.get("meta") or {}
            stamp = entry.get("mtime", 0)
            try:
                when = _dt.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                when = "?"
            kb = entry.get("size", 0) // 1024
            row = (f"{_Path(entry['path']).name}  ·  "
                   f"{meta.get('db_name', '?')}  ·  {kb} KB  ·  {when}")
            file_list.addItem(row)
            item = file_list.item(file_list.count() - 1)
            item.setData(Qt.UserRole, entry["path"])
            item.setToolTip(entry["path"])
        if not files:
            file_list.addItem("No backup files yet.")
        layout.addWidget(file_list, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_restore = QPushButton("Restore…")
        btn_restore.clicked.connect(
            lambda: self._browse_restore(dlg, instance_id, file_list))
        btn_row.addWidget(btn_restore)
        btn_del = QPushButton("Delete")
        btn_del.setProperty("role", "destructive")
        btn_del.clicked.connect(
            lambda: self._browse_delete(dlg, instance_id, file_list))
        btn_row.addWidget(btn_del)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
        dlg.exec()

    def _browse_selection(self, file_list):
        item = file_list.currentItem()
        if item is None:
            return None
        path = item.data(Qt.UserRole)
        if not path or not str(path).endswith(".dump"):
            return None
        return str(path)

    def _browse_restore(self, dlg, instance_id: str, file_list) -> None:
        dump = self._browse_selection(file_list)
        if dump is None:
            self.message.emit("Select a backup file first")
            return


        parent = dlg if isinstance(dlg, QWidget) else None
        target, ok = QInputDialog.getText(
            parent, "Restore database",
            "Target database (will be DROPPED and recreated — never "
            "merged):")
        target = (target or "").strip()
        if not ok or not target:
            return
        if not ask_confirm_typed(
                parent, "Restore database?",
                f"Retype the target name to restore into '{target}'.",
                target, "Restore (drop + recreate)"):
            return
        self._restore_dump(instance_id, dump, target)

    def _restore_dump(self, instance_id: str, dump: str,
                      target: str) -> None:

        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        self.message.emit(f"Restoring into {target}…")

        def _work():
            return db_backup.restore_database(
                dump, target, db_user=inst.db_user, db_password=pw)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refresh_schedules(instance_id)

        run_in_background(self, _work, _done)

    def _browse_delete(self, dlg, instance_id: str, file_list) -> None:

        dump = self._browse_selection(file_list)
        if dump is None:
            self.message.emit("Select a backup file first")
            return
        parent = dlg if isinstance(dlg, QWidget) else None
        if not ask_confirm(
                parent, "Delete this backup file?",
                f"{_Path(dump).name}\nThis cannot be undone.",
                "Delete backup", destructive=True):
            return

        def _work():
            return backup_scheduler.delete_backup_file(dump)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)

        run_in_background(self, _work, _done)
