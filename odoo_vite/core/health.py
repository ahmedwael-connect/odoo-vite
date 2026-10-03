"""Instance health monitor (3.1.0 C2): cheap, read-only probes.

One :class:`HealthReport` per instance bundling process, virtualenv,
PostgreSQL reachability, disk headroom and log freshness/error checks.
Everything is best-effort — a raising probe degrades to an ``unknown``
check, never an exception — so the Overview health strip can poll it
freely (facade: ``api.app.health(instance_id)``).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import get_instance, list_instances

# thresholds
DISK_WARN_MB = 1024       # < 1 GiB free → warn
DISK_ERROR_MB = 200       # < 200 MiB free → error
LOG_STALE_S = 900         # running instance, log untouched for 15 min
LOG_TAIL_BYTES = 65536    # error scan window
LOG_ERR_WARN = 10         # recent error lines → warn

# worst-first aggregation
_LEVEL_RANK = {"error": 4, "warn": 3, "unknown": 2, "ok": 1, "info": 0}


@dataclass
class Check:
    name: str
    state: str = "unknown"   # ok | warn | error | unknown | info
    detail: str = ""


@dataclass
class HealthReport:
    instance_id: str
    level: str = "unknown"
    summary: str = ""
    checks: list[Check] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "level": self.level,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "state": c.state, "detail": c.detail}
                for c in self.checks
            ],
        }


def _safe(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — probes must never raise
        return Check(name, "unknown", f"{type(exc).__name__}: {exc}")


def _check_process(pid: int | None) -> Check:
    if pid:
        return Check("process", "ok", f"running (pid {pid})")
    return Check("process", "info", "stopped")


def _check_venv(inst: Instance) -> Check:
    venv = Path(str(inst.venv_path)).expanduser() if inst.venv_path else None
    python = venv / "bin" / "python" if venv else None
    if python and python.exists():
        return Check("venv", "ok", "python installed")
    return Check("venv", "warn", "venv python missing — rebuild the venv")


def _check_postgres() -> Check:
    from odoo_vite.core.db_manager import server_reachable

    if server_reachable():
        return Check("postgres", "ok", "reachable")
    return Check("postgres", "error", "unreachable — database ops will fail")


def _check_disk(inst: Instance) -> Check:
    target = Path(str(inst.path)).expanduser() if inst.path else Path.home()
    if not target.exists():
        target = Path.home()
    free_mb = shutil.disk_usage(target).free // (1024 * 1024)
    if free_mb < DISK_ERROR_MB:
        return Check("disk", "error", f"{free_mb} MB free")
    if free_mb < DISK_WARN_MB:
        return Check("disk", "warn", f"{free_mb} MB free")
    if free_mb >= 1024 * 1024:
        return Check("disk", "ok", f"{free_mb // (1024 * 1024)} TB free")
    return Check("disk", "ok", f"{free_mb // 1024} GB free")


def _check_log(inst: Instance, running: bool) -> Check:
    raw = str(inst.log_path or "").strip()
    if not raw:
        return Check("log", "info", "no log path recorded")
    path = Path(raw).expanduser()
    if not path.exists():
        return Check("log", "info", "no log file yet")
    stat = path.stat()
    age = time.time() - stat.st_mtime
    with path.open("rb") as fh:
        if stat.st_size > LOG_TAIL_BYTES:
            fh.seek(stat.st_size - LOG_TAIL_BYTES)
        tail = fh.read().decode("utf-8", "replace")
    errors = sum(
        1 for line in tail.splitlines() if " ERROR " in line or " CRITICAL " in line
    )
    if not running:
        return Check("log", "info", f"stopped — {errors} recent errors in tail")
    if errors >= LOG_ERR_WARN:
        return Check("log", "warn", f"{errors} recent error lines")
    if age > LOG_STALE_S:
        return Check("log", "warn", f"no writes for {int(age)}s")
    return Check("log", "ok", f"fresh ({errors} recent errors)")


def _worst(checks: list[Check]) -> str:
    if not checks:
        return "unknown"
    return max((c.state for c in checks), key=lambda s: _LEVEL_RANK.get(s, 0))


def _summary(level: str, checks: list[Check]) -> str:
    warns = sum(1 for c in checks if c.state == "warn")
    errors = sum(1 for c in checks if c.state == "error")
    unknowns = sum(1 for c in checks if c.state == "unknown")
    if level == "error":
        return f"{errors} error(s) — attention needed"
    if level == "warn":
        return f"{warns} warning(s)"
    if level == "unknown":
        return f"{unknowns} check(s) could not run"
    return f"all {len(checks)} checks healthy"


def check_instance(instance_id: str, db_path=None) -> HealthReport:
    inst = get_instance(instance_id, db_path)
    if inst is None:
        return HealthReport(
            instance_id,
            level="error",
            summary="instance not found",
            checks=[Check("instance", "error", "not found")],
        )

    from odoo_vite.core.process_manager import _alive_pid

    pid = _alive_pid(inst)
    running = pid is not None
    checks = [
        _safe("process", lambda: _check_process(pid)),
        _safe("venv", lambda: _check_venv(inst)),
        _safe("postgres", _check_postgres),
        _safe("disk", lambda: _check_disk(inst)),
        _safe("log", lambda: _check_log(inst, running)),
    ]
    level = _worst(checks)
    return HealthReport(inst.id, level, _summary(level, checks), checks)


def check_all(db_path=None) -> list[HealthReport]:
    reports = []
    for inst in list_instances(db_path):
        reports.append(check_instance(inst.id, db_path))
    return reports
