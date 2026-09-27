"""Database flows (PSQ-4.4): faithful port of GTK database_ops.py.

Ground-truth rule (PM): db_state.py is asked, never re-derived — zero new
existence/initialized logic here. Destructive paths (drop/restore) go
through ask_confirm_typed on the GUI thread before any worker starts;
verify-then-act (existence check) happens inside the worker, same as GTK.
"""

from PySide6.QtCore import QObject, Signal

from odoo_vite.ui_qt.workers import run_in_background
from odoo_vite.core import db_backup, db_manager, process_manager  # noqa: E402
from odoo_vite.core.db_state import (  # noqa: E402
    get_db_state, human_size, odoo_major, validate_db_config)
from odoo_vite.core.registry import (  # noqa: E402
    get_db_password, get_instance)
from odoo_vite.core.result import Result  # noqa: E402


def group_discover_entries(entries: list, instance_version: str) -> dict:
    """Pure A.1 grouping: likely/other/plain. Only 'likely' pre-checks.

    likely = initialized AND major matches the instance version.
    (Reimplemented here, not imported from GTK ui/ — ui_qt must not
    depend on ui/, and gi must never load in the Qt process.)
    """
    me = (instance_version or "").strip()
    groups = {"likely": [], "other": [], "plain": []}
    for entry in entries or []:
        name = entry.get("name", "")
        if entry.get("initialized") and entry.get("odoo_major"):
            key = "likely" if entry["odoo_major"] == me else "other"
        else:
            key = "plain"
        groups[key].append(name)
    for key in groups:
        groups[key] = sorted(groups[key])
    return groups


class DatabaseFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    refreshRequested = Signal()
    statesReady = Signal(str, dict)  # (instance_id, states)
    reportReady = Signal(str, dict)  # (instance_id, validate report)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------ refresh

    def refresh_states(self, instance_id: str) -> None:
        # Keyring resolves HERE (GUI thread) — never inside _work.
        # Secret Service dbus calls from worker threads hang/abort.
        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit("Instance not found")
            return
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None

        def _work():
            names = list(dict.fromkeys(
                (inst.tracked_dbs or [])
                + ([inst.primary_db] if inst.primary_db else [])))
            states: dict = {}
            for name in names:
                try:
                    st = get_db_state(name, inst.db_user, pw)
                except Exception:
                    continue
                states[name] = {
                    "exists": st.exists,
                    "initialized": st.initialized,
                    "odoo_version": st.odoo_version or "",
                    "odoo_major": odoo_major(st.odoo_version),
                    "size": human_size(st.size_bytes),
                    "owner": st.owner or "",
                }
            return Result(ok=True, message="", data={"states": states})

        def _done(ok: bool, _message: str, data: dict) -> None:
            if ok:
                self.statesReady.emit(instance_id, data.get("states", {}))

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ ops

    def init_db(self, instance_id: str, db_name: str) -> None:

        self.message.emit(f"Initializing {db_name}…")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, process_manager.initialize_database,
                          _done, instance_id, db_name)

    def drop_db(self, instance_id: str, db_name: str) -> None:
        """Assumes typed-confirm already happened on the GUI thread."""

        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit("Instance not found")
            return
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        self.message.emit(f"Dropping {db_name}…")

        def _work():
            # Verify-then-act: never drop blind (GTK Part A parity).
            if not get_db_state(db_name, inst.db_user, pw).exists:
                return Result(ok=True,
                              message=f"'{db_name}' does not exist — "
                                      "nothing to drop")
            return db_manager.drop_database(db_name, inst.db_user, pw)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def backup_db(self, instance_id: str, db_name: str, dest: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit("Instance not found")
            return
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        self.message.emit(f"Backing up {db_name}…")

        def _work():
            return db_backup.backup_database(
                db_name, dest, db_user=inst.db_user, db_password=pw,
                instance_id=inst.id, instance_name=inst.name)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def restore_db(self, instance_id: str, dump: str, target: str) -> None:
        """Assumes target typed-confirm already happened on the GUI thread."""

        inst = get_instance(instance_id)
        if inst is None:
            self.message.emit("Instance not found")
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
            self.refreshRequested.emit()

        run_in_background(self, _work, _done)

    def validate(self, instance_id: str) -> None:

        self.message.emit("Validating…")

        def _work():

            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance not found")
            try:
                report = validate_db_config(inst)
            except Exception as exc:
                return Result.failure(f"Validation crashed: {exc}")
            return Result(ok=True, message="", data={"report": report})

        def _done(ok: bool, message: str, data: dict) -> None:
            if ok:
                self.reportReady.emit(instance_id,
                                      data.get("report", {}))
            else:
                self.message.emit(message)

        run_in_background(self, _work, _done)
