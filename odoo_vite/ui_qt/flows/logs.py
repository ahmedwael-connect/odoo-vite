"""Log flows (PSQ-7.3): faithful port of GTK logs_monitoring.py.

Search/doctor/slow-query/profile run in workers (file scans, psql,
py-spy); results render on the page via signals. SVG flame graphs open
in the system viewer (QDesktopServices — no new QtSvg dependency, same
as GTK shelling out to the default app). Core imports at module top —
never first-import from a worker thread.
"""

from PySide6.QtCore import QObject, Signal

from odoo_vite.core import db_manager, log_doctor, log_search, profiler
from odoo_vite.core.process_manager import _alive_pid
from odoo_vite.core.registry import get_db_password, get_instance
from odoo_vite.core.result import Result
from odoo_vite.ui_qt.workers import run_in_background


class LogFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    searchReady = Signal(str, bool, str, list)  # (id, ok, message, matches)
    doctorReady = Signal(str, list)  # (id, findings)
    slowReady = Signal(str, bool, str, list)  # (id, ok, message, rows)
    profileDone = Signal(str, bool, str, str)  # (id, ok, message, svg)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    # ------------------------------------------------------------ search

    def search(self, instance_id: str, pattern: str,
               level: str | None) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        if not (pattern or "").strip():
            self.message.emit("Enter a regex pattern to search")
            return
        self.message.emit("Searching…")

        def _work():
            res = log_search.search_file(inst.log_path or "", pattern,
                                         level=level, context=2)
            return Result(ok=res.ok, message=res.message, data={
                "matches": (res.data or {}).get("matches", [])
                if res.ok else []})

        def _done(ok: bool, message: str, data: dict) -> None:
            self.searchReady.emit(instance_id, ok, message,
                                  data.get("matches", []))

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ doctor

    def doctor(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None or not inst.log_path:
            self.message.emit("No log file recorded for this instance")
            return
        self.message.emit("Scanning…")

        def _work():
            try:
                findings = log_doctor.diagnose_file(inst.log_path)
            except Exception as exc:
                return Result.failure(f"Doctor failed: {exc}")
            return Result(ok=True, message="", data={"findings": findings})

        def _done(ok: bool, message: str, data: dict) -> None:
            if not ok:
                self.message.emit(message)
                return
            findings = data.get("findings", [])
            self.doctorReady.emit(instance_id, findings)
            if not findings:
                self.message.emit("Doctor found no known issues in the log")

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ slow queries

    def slow_refresh(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.message.emit("Querying pg_stat_statements…")

        def _work():
            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            if not db_manager.pg_stat_statements_enabled(inst.db_user, pw):
                return Result.failure(
                    "pg_stat_statements is not available to this role. "
                    "Ask your Postgres admin to run: CREATE EXTENSION "
                    "pg_stat_statements; (first enable needs "
                    "shared_preload_libraries + a server restart)")
            res = db_manager.slow_queries(inst.primary_db, inst.db_user, pw)
            return Result(ok=res.ok, message=res.message, data={
                "rows": (res.data or {}).get("queries", []) if res.ok else []})

        def _done(ok: bool, message: str, data: dict) -> None:
            self.slowReady.emit(instance_id, ok, message,
                                data.get("rows", []))

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ profile

    def profile(self, instance_id: str, duration: int = 10) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        pid = _alive_pid(inst)
        if pid is None:
            self.message.emit(
                "Profile needs a running process — start the instance first.")
            return
        if profiler.py_spy_path() is None:
            self.message.emit(
                "py-spy is not installed — install it with "
                "'pip install py-spy' (user-scoped, no sudo), then retry")
            return
        self.message.emit(f"Profiling pid {pid} for {duration}s…")

        def _work():
            import os
            import tempfile

            dest = os.path.join(
                tempfile.gettempdir(),
                f"odoo-vite-profile-{instance_id[:8]}.svg")
            res = profiler.profile_pid(pid, duration=duration,
                                       output_svg=dest)
            return Result(ok=res.ok, message=res.message, data={
                "svg": (res.data or {}).get("svg", "") if res.ok else ""})

        def _done(ok: bool, message: str, data: dict) -> None:
            self.profileDone.emit(instance_id, ok, message,
                                  data.get("svg", ""))

        run_in_background(self, _work, _done)

    def view_svg(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.message.emit(f"Profile saved: {path}")
        else:
            self.message.emit(f"Opened {path} externally")
