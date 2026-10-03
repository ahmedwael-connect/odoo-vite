"""Log operations (PSS-6a): async core drivers for the UI facade.

Mirrors Qt's LogFlows + LogsPage contracts (odoo_vite.ops must not import
ui_qt, which pulls PySide6):

- Tailing stays OUT of this module: LogFollower is driven by the bridge's
  1s UI-thread Timer (file reads are cheap and synchronous, Qt page
  parity) — never on a worker (the follower is explicitly not
  thread-safe; one poll loop owns it).
- search / doctor / slow-refresh / profile run async with typed sinks:
  on_search(iid, ok, message, matches), on_doctor(iid, findings),
  on_slow(iid, ok, message, rows), on_profile(iid, ok, message, svg).
- Display formatting is pure and Qt-parity-tested (caps, prefixes).

Divergence from Qt (documented choice): scroll-up auto-pause. Qt read the
QScrollBar to pause when the user scrolls up; Slint's ListView exposes no
scroll position, so Follow is an explicit toggle (default on) — polls
still run while paused, appends just stop.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

from odoo_vite.core import db_manager, log_doctor, log_search, profiler
from odoo_vite.core.process_manager import _alive_pid
from odoo_vite.core.registry import get_db_password, get_instance
from odoo_vite.core.result import Result

LOG_LEVELS = ["All levels", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
PROFILE_DURATIONS = ["5s", "10s", "30s"]
LOG_TAIL_CAP = 2000  # Qt keeps 5000 QStrings; Slint rows are heavier —
# the cap is display-only, the file is untouched either way.
LINE_CAP = 2000


def format_search_lines(matches: list) -> list[str]:
    """Qt set_search_results parity: capped, truncated, context indented."""
    lines = []
    for match in (matches or [])[:200]:
        lines.append(
            f"line {match.get('lineno', '?')}: "
            f"{match.get('line', '')}"[:220])
        for ctx in (match.get("before", []) + match.get("after", []))[-4:]:
            lines.append(f"    {ctx}"[:220])
    return lines


def format_doctor_lines(findings: list) -> list[str]:
    """Qt set_doctor_findings parity: severity mark + count."""
    lines = []
    for finding in findings or []:
        count = finding.get("count", 1)
        mark = "!" if finding.get("severity") == "high" else "i"
        lines.append(
            f"[{mark}] {finding.get('title', '')}"
            + (f"  (×{count})" if count > 1 else ""))
    return lines


def format_slow_lines(rows: list) -> list[str]:
    """Qt set_slow_queries parity: capped at 20 formatted rows."""
    return [f"{str(entry.get('query', ''))[:140]} — "
            f"{entry.get('calls', 0)} calls · "
            f"total {entry.get('total_ms', 0)} ms"
            for entry in (rows or [])[:20]]


def parse_profile_duration(text: str) -> int:
    """'10s' → 10 (Qt _on_profile_clicked parity, digits only)."""
    try:
        return int("".join(c for c in (text or "") if c.isdigit()) or 10)
    except ValueError:
        return 10


class LogOps:
    def __init__(self, on_message=None, on_search=None, on_doctor=None,
                 on_slow=None, on_profile=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._search = on_search or (lambda _i, _o, _m, _r: None)
        self._doctor = on_doctor or (lambda _i, _f: None)
        self._slow = on_slow or (lambda _i, _o, _m, _r: None)
        self._profile = on_profile or (lambda _i, _o, _m, _s: None)

    def _instance_pw(self, instance_id: str):
        """Resolve (instance, password) on the caller thread (Qt dbus
        rule — Secret Service calls never happen on workers)."""
        inst = get_instance(instance_id)
        if inst is None:
            return None, None
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        return inst, pw

    async def search(self, instance_id: str, pattern: str,
                     level: str | None):
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        if not (pattern or "").strip():
            self._message("Enter a regex pattern to search", "error")
            return Result.failure("Enter a regex pattern to search")
        if level == LOG_LEVELS[0]:
            level = None
        self._message("Searching…", "info")

        def _work():
            return log_search.search_file(inst.log_path or "", pattern,
                                          level=level, context=2)

        res = await asyncio.to_thread(_work)
        if res.ok:
            matches = (res.data or {}).get("matches", [])
        else:
            matches = []
            self._message(res.message, "error")
        self._search(instance_id, res.ok, res.message, matches)
        return res

    async def doctor(self, instance_id: str):
        inst = get_instance(instance_id)
        if inst is None or not inst.log_path:
            self._message("No log file recorded for this instance", "error")
            return Result.failure("No log file recorded")
        self._message("Scanning…", "info")

        def _work():
            try:
                return Result(ok=True, message="",
                              data={"findings": log_doctor.diagnose_file(
                                  inst.log_path)})
            except Exception as exc:
                return Result.failure(f"Doctor failed: {exc}")

        res = await asyncio.to_thread(_work)
        if not res.ok:
            self._message(res.message, "error")
            return res
        findings = (res.data or {}).get("findings", [])
        self._doctor(instance_id, findings)
        if not findings:
            self._message("Doctor found no known issues in the log", "info")
        return res

    async def slow_refresh(self, instance_id: str):
        inst, pw = self._instance_pw(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        self._message("Querying pg_stat_statements…", "info")

        def _work():
            if not db_manager.pg_stat_statements_enabled(inst.db_user, pw):
                return Result.failure(
                    "pg_stat_statements is not available to this role. "
                    "Ask your Postgres admin to run: CREATE EXTENSION "
                    "pg_stat_statements; (first enable needs "
                    "shared_preload_libraries + a server restart)")
            res = db_manager.slow_queries(inst.primary_db, inst.db_user, pw)
            return Result(ok=res.ok, message=res.message, data={
                "rows": (res.data or {}).get("queries", [])
                if res.ok else []})

        res = await asyncio.to_thread(_work)
        if not res.ok:
            self._message(res.message, "error")
        self._slow(instance_id, res.ok, res.message,
                   (res.data or {}).get("rows", []))
        return res

    async def profile(self, instance_id: str, duration: int = 10):
        inst = get_instance(instance_id)
        if inst is None:
            self._message("Instance disappeared", "error")
            return Result.failure("Instance disappeared")
        pid = _alive_pid(inst)
        if pid is None:
            self._message("Profile needs a running process — start the "
                          "instance first.", "error")
            return Result.failure("Instance is not running")
        if profiler.py_spy_path() is None:
            # 3.1.0 N2: install on demand instead of demanding a manual pip.
            self._message("py-spy missing — installing it now (user pip)…",
                          "info")

            def _ensure():
                return profiler.ensure_py_spy(
                    progress_cb=lambda line: self._message(line.rstrip(),
                                                            "info"))

            ensure = await asyncio.to_thread(_ensure)
            if not ensure.ok:
                self._message(ensure.message, "error")
                return ensure
            self._message(ensure.message, "info")
        self._message(f"Profiling pid {pid} for {duration}s…", "info")

        def _work():
            dest = os.path.join(
                tempfile.gettempdir(),
                f"odoo-vite-profile-{instance_id[:8]}.svg")
            return profiler.profile_pid(pid, duration=duration,
                                        output_svg=dest)

        res = await asyncio.to_thread(_work)
        if not res.ok:
            self._message(res.message, "error")
            self._profile(instance_id, False, res.message, "")
            return res
        svg = (res.data or {}).get("svg", "")
        self._profile(instance_id, True, res.message, svg)
        return res


async def open_svg_external(queue, path: str) -> None:
    """Flame graphs open in the system viewer (Qt QDesktopServices parity
    via xdg-open — no new dependencies). Runs on the loop thread."""

    def _work():
        try:
            proc = subprocess.run(
                ["xdg-open", path], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Profile saved: {path} (could not open: {exc})"
        if proc.returncode == 0:
            return f"Opened {path} externally"
        return f"Profile saved: {path}"

    queue.put(("message", (await asyncio.to_thread(_work), "info")))
