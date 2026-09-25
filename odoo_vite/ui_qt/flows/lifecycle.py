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

from odoo_vite.ui_qt.workers import run_in_background
from odoo_vite.core import (  # noqa: E402
    db_manager, process_manager, removal)
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

    def switch(self, instance_id: str, db_name: str) -> None:

        self.message.emit(f"Switching to {db_name}…")
        self._run_op("switch", process_manager.switch_database,
                     instance_id, db_name, confirm_cb=self._confirm_cb)

    def track(self, instance_id: str, db_name: str) -> None:

        self._run_op("track", track_database, instance_id, db_name)

    def untrack(self, instance_id: str, db_name: str) -> None:

        self._run_op("untrack", untrack_database, instance_id, db_name)

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

        def _work():

            inst = get_instance(instance_id)
            if inst is None:
                return Result.failure("Instance not found")
            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
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
