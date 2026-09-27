"""Lifecycle flows (PSQ-3.4): start/stop/restart/remove/switch/track/discover.

Qt side of the GTK instance_lifecycle.py flows. Execution via
ui_qt.workers (QThread + queued Result); the GTK blocking-confirm pattern
(_ask_blocking) becomes confirmNeeded -> main-window dialog -> reply, with
the worker parked on a threading.Event. First-start and switch confirms
keep working without ever touching widgets off the main thread.
"""

import threading
import uuid

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.ui_qt.widgets.dialogs import ask_confirm  # noqa: E402
from odoo_vite.ui_qt.widgets.selection_list import (  # noqa: E402
    SelectionList,
)

from odoo_vite.ui_qt.workers import run_in_background
from odoo_vite.core import (  # noqa: E402
    db_manager, process_manager, removal)
from odoo_vite.core import clone as clone_core  # noqa: E402
from odoo_vite.core import provisioning as provisioning_core  # noqa: E402
from odoo_vite.core import venv_manager as venv_manager_core  # noqa: E402
from odoo_vite.ui_qt.widgets.progress_dialog import (  # noqa: E402
    ProgressDialog,
)
from odoo_vite.core.db_manager import (  # noqa: E402
    track_database, untrack_database)
from odoo_vite.core.db_state import get_db_state, odoo_major  # noqa: E402
from odoo_vite.core.registry import (  # noqa: E402
    get_db_password, get_instance)
from odoo_vite.core.result import Result  # noqa: E402


class LifecycleFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)  # toast/status line
    refreshRequested = Signal()  # re-read registry, update sidebar+page
    # confirmNeeded payload: {"key": str, "heading": str, "body": str}
    confirmNeeded = Signal(dict)
    diskReady = Signal(str, str)  # (instance_id, "Disk: …" label text)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: dict[str, tuple[threading.Event, list]] = {}

    # ------------------------------------------------------------ confirms

    def reply_confirm(self, key: str, confirmed: bool) -> None:
        """Main-window side: answer a confirmNeeded request."""
        pending = self._pending.pop(key, None)
        if pending is not None:
            event, box = pending
            box.append(bool(confirmed))
            event.set()

    def _blocking_confirm(self, heading: str, body: str) -> bool:
        """Worker-thread side: ask the GUI, wait (max 5 min, like GTK)."""
        key = uuid.uuid4().hex
        event = threading.Event()
        box: list = []
        self._pending[key] = (event, box)
        self.confirmNeeded.emit({"key": key, "heading": heading,
                                 "body": body})
        if not event.wait(timeout=300):
            self._pending.pop(key, None)
            return False
        return bool(box and box[0])

    def _confirm_cb(self, preview: dict) -> bool:
        dbs = preview.get("databases", "?")
        return self._blocking_confirm(
            "Confirm database action",
            f"Database: {dbs}\n\n{preview.get('detail', '')}".strip())

    # ------------------------------------------------------------ simple ops

    def _run_op(self, label: str, fn, *args, **kwargs) -> None:
        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, fn, _done, *args, **kwargs)

    def start(self, instance_id: str, database: str | None = None) -> None:

        self.message.emit("Starting…")
        self._run_op("start", process_manager.start_instance, instance_id,
                     database=database, confirm_cb=self._confirm_cb)

    def stop(self, instance_id: str) -> None:

        self.message.emit("Stopping…")
        self._run_op("stop", process_manager.stop_instance, instance_id)

    def restart(self, instance_id: str) -> None:

        self.message.emit("Restarting…")
        self._run_op("restart", process_manager.restart_instance,
                     instance_id)

    def remove(self, instance_id: str, drop_db: bool = False) -> None:
        self.message.emit("Removing…")
        self._run_op("remove", removal.remove_instance, instance_id,
                     drop_db=drop_db)

    def clone(self, instance_id: str, new_name: str,
              new_port: int | None = None) -> None:
        """Duplicate files + settings in a worker (copies can be GBs).

        Keyring resolves HERE (GUI thread) — never inside _work.
        """
        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit(f"No instance with id '{instance_id}'")
            return
        try:
            password = get_db_password(inst)
        except Exception:
            password = ""
        self.message.emit(f"Cloning '{inst.name}'…")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(
            self, clone_core.clone_instance, _done, instance_id, new_name,
            new_port=new_port, src_password=password)

    def clone_dialog(self, parent_widget, instance_id: str) -> None:
        """Name + port picker with the honest scope note (no DB copy,
        venv rebuilt before first start of the clone)."""
        inst = get_instance(instance_id)
        if inst is None:
            return
        if (inst.status or "") == "running":
            self.message.emit(
                f"Stop '{inst.name}' before cloning it")
            return
        parent = (parent_widget if isinstance(parent_widget, QWidget)
                  else None)
        from PySide6.QtWidgets import QFormLayout, QSpinBox

        dlg = QDialog(parent)
        dlg.setWindowTitle(f"Clone '{inst.name}'")
        dlg.setMinimumWidth(460)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        form = QFormLayout()
        form.setSpacing(8)
        name_entry = QLineEdit(f"{inst.name} (clone)")
        form.addRow("Name:", name_entry)
        port_spin = QSpinBox()
        port_spin.setRange(1024, 65535)
        try:
            port_spin.setValue(
                provisioning_core.suggest_port((inst.port or 8069) + 1))
        except Exception:
            port_spin.setValue((inst.port or 8069) + 1)
        form.addRow("Port:", port_spin)
        layout.addLayout(form)
        note = QLabel(
            "Copies files + settings only — databases are NOT copied, "
            "and the venv is rebuilt before the clone's first start.")
        note.setWordWrap(True)
        note.setProperty("class", "dim")
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Clone")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        new_name = name_entry.text().strip()
        if not new_name:
            self.message.emit("Clone needs a name — cancelled")
            return
        self.clone(instance_id, new_name, int(port_spin.value()))

    def rebuild_venv_dialog(self, parent_widget, instance_id: str) -> None:
        """U5.2: confirm exact commands, then stream rebuild_venv into a
        ProgressDialog (minutes + network — never silent)."""
        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit(f"No instance with id '{instance_id}'")
            return
        if (inst.status or "") == "running":
            self.message.emit(
                f"Stop '{inst.name}' before rebuilding its venv")
            return
        parent = (parent_widget if isinstance(parent_widget, QWidget)
                  else None)
        venv = f"{inst.path}/venv" if inst.path else "<instance>/venv"
        req = (f"{inst.community_path}/requirements.txt"
               if inst.community_path else "<community>/requirements.txt")
        if not ask_confirm(
                parent, f"Rebuild venv for '{inst.name}'?",
                f"Runs:\npython3 -m venv {venv}\n"
                f"{venv}/bin/pip install -r {req}\n\n"
                "The existing venv (if any) is deleted first. Takes "
                "minutes and needs network access.",
                "Rebuild"):
            return
        dlg = ProgressDialog(parent, f"Rebuilding venv — {inst.name}")
        dlg.show()

        def _work():
            return venv_manager_core.rebuild_venv(
                instance_id, progress_cb=dlg.request_append.emit)

        def _done(ok: bool, message: str, _data: dict) -> None:
            dlg.request_done.emit(ok, message)
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def remove_dialog(self, parent_widget, instance_id: str) -> None:
        """GTK remove parity: adopted = plain confirm; managed = typed
        name + per-database drop checkboxes (all unchecked by default)."""
        inst = get_instance(instance_id)
        if inst is None:
            return
        parent = (parent_widget if isinstance(parent_widget, QWidget)
                  else None)
        if (inst.mode or "managed").lower() == "adopted":
            if ask_confirm(
                    parent, f"Remove '{inst.name}' from Odoo Vite?",
                    "Your files and database will NOT be touched.",
                    "Remove"):
                self.remove(instance_id)
            return
        dlg = QDialog(parent)
        dlg.setWindowTitle(f"Remove '{inst.name}'?")
        dlg.setMinimumWidth(480)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        entry = QLineEdit()
        entry.setPlaceholderText(f"Type '{inst.name}' to confirm")
        layout.addWidget(entry)
        layout.addWidget(QLabel(
            "Databases to drop (all unchecked by default — unchecked "
            "databases are kept):"))
        picker = SelectionList(multi=True, searchable=False, parent=dlg)
        tracked = list(inst.tracked_dbs or [])
        if inst.primary_db and inst.primary_db not in tracked:
            tracked.append(inst.primary_db)
        picker.set_items([
            {"id": db, "title": db + ("  (primary)" if db == inst.primary_db
                                      else ""), "checked": False}
            for db in tracked])
        layout.addWidget(picker)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Remove")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        if entry.text().strip() != inst.name:
            self.message.emit("Name did not match — remove cancelled")
            return
        drop = picker.checked_ids()
        self.message.emit("Removing…")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(
            self, removal.remove_instance, _done, instance_id,
            drop_db=(inst.primary_db in drop),
            drop_extra_dbs=[d for d in drop if d != inst.primary_db])

    def switch(self, instance_id: str, db_name: str) -> None:

        self.message.emit(f"Switching to {db_name}…")
        self._run_op("switch", process_manager.switch_database,
                     instance_id, db_name, confirm_cb=self._confirm_cb)

    def track(self, instance_id: str, db_name: str) -> None:

        self._run_op("track", track_database, instance_id, db_name)

    def untrack(self, instance_id: str, db_name: str) -> None:

        self._run_op("untrack", untrack_database, instance_id, db_name)

    def measure_disk(self, instance_id: str) -> None:
        """Folder-size breakdown in a worker (large trees take seconds)."""
        from odoo_vite.core import disk_usage as disk_usage_core

        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit(f"No instance with id '{instance_id}'")
            return
        self.message.emit("Measuring disk usage…")

        def _done(ok: bool, message: str, data: dict) -> None:
            human = data.get("human", "") if ok else ""
            self.diskReady.emit(
                instance_id, f"Disk: {human}" if ok and human else message)
            if not ok:
                self.message.emit(message)

        run_in_background(self, disk_usage_core.measure_instance, _done,
                          inst)

    def track_many(self, instance_id: str, db_names: list) -> None:
        """Track several DBs in ONE worker, sequentially.

        track_database is read-modify-write on tracked_dbs — N parallel
        workers lose updates (verified live: 1 of 3 survived). Batching
        here, not in core/ (which stays single-op by design).
        """

        def _work():
            done, failed = [], []
            for name in db_names:
                res = track_database(instance_id, name)
                (done if res.ok else failed).append(name)
            if failed and not done:
                return Result.failure(
                    f"Could not track: {', '.join(failed)}")
            msg = f"Tracking {len(done)} database(s)"
            if failed:
                msg += f" ({len(failed)} failed: {', '.join(failed)})"
            return Result(ok=True, message=msg, data={"tracked": done})

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ discover

    def discover_entries(self, instance_id: str,
                         on_ready) -> None:
        """Fetch untracked-database entries, then call on_ready(payload)
        on the GUI thread. The caller shows the SelectionList dialog."""
        # Keyring resolves HERE (GUI thread) — never inside _work.
        # Secret Service dbus calls from worker threads hang/abort.
        inst = get_instance(instance_id)
        if inst is None:
            on_ready({"ok": False, "message": "Instance not found",
                      "entries": []})
            return
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None

        def _work():
            res = db_manager.list_databases_for_user(inst.db_user, pw)
            if not res.ok:
                return Result.failure(res.message)
            tracked = set(inst.tracked_dbs or [])
            entries = []
            for name in (res.data or {}).get("databases", []):
                if name in tracked:
                    continue
                entry = {"name": name, "initialized": False,
                         "odoo_major": ""}
                try:
                    st = get_db_state(name, inst.db_user, pw)
                    entry["initialized"] = bool(st.initialized)
                    entry["odoo_major"] = odoo_major(st.odoo_version)
                except Exception:
                    pass
                entries.append(entry)
            return Result(ok=True,
                          message=f"{len(entries)} untracked found",
                          data={"entries": entries})

        def _done(ok: bool, message: str, data: dict) -> None:
            on_ready({"ok": ok, "message": message,
                      "entries": data.get("entries", [])})

        run_in_background(self, _work, _done)
