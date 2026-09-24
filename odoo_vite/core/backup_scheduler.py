"""Scheduled backups: data model + cron math + retention (Sprint BK.1).

No GTK imports. Scheduling *execution* lives in BK.2 (systemd timer +
headless runner); this module is the mechanism-independent core:
- cron-expression parsing (5-field, numeric) + next-run computation
- backup_schedules registry table (CRUD + run-status tracking)
- retention victim selection (pure) + prune with audit logging
- run_schedule(): the single "execute one schedule now" entry used by both
  the headless runner and the UI's Run-Now button.

Compression interpretation (spec BK.1): pg_dump's `-Fc` custom format IS
the compression — no second compression step (gzipping the dump would
break restore_database(), which expects `-Fc`). The stored `format`
field is therefore fixed at "custom" in v1 and documents that choice.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from odoo_vite.core.registry import _connect, get_db_password, get_instance
from odoo_vite.core.result import Result

TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_schedules (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL DEFAULT '',
    databases TEXT NOT NULL DEFAULT '[]',
    cron TEXT NOT NULL DEFAULT '',
    retention_n INTEGER NOT NULL DEFAULT 7,
    retention_days INTEGER NOT NULL DEFAULT 0,
    dest_dir TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_BACKUPS_ROOT = "~/.local/share/odoo-vite/backups"

# ------------------------------------------------------------------ cron math

_MONTH_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4,
              "fri": 5, "sat": 6}
# cron Sunday is 0 or 7; datetime Monday=0..Sunday=6 -> convert at match time.
_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _parse_field(text: str, lo: int, hi: int,
                 names: dict | None = None) -> set[int]:
    """Parse one cron field: `*`, `*/n`, `a,b`, `a-b`, `a-b/n`, plain int."""
    values: set[int] = set()

    def token_value(tok: str) -> int:
        tok = tok.strip().lower()
        if names and tok in names:
            return names[tok]
        val = int(tok)
        if not lo <= val <= hi:
            raise ValueError(f"{val} out of range {lo}-{hi}")
        return val

    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field part")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError("cron step must be >= 1")
            part = part or "*"
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            start, end = token_value(a_s), token_value(b_s)
            if start > end:
                raise ValueError("cron range start after end")
        else:
            start = end = token_value(part)
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("cron field matched nothing")
    return values


def parse_cron(expr: str) -> dict:
    """Parse a 5-field cron expression; raises ValueError on any problem."""
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression needs 5 fields (minute hour day month weekday), "
            f"got {len(parts)}: {expr!r}")
    minute = _parse_field(parts[0], *_FIELD_RANGES[0])
    hour = _parse_field(parts[1], *_FIELD_RANGES[1])
    dom = _parse_field(parts[2], *_FIELD_RANGES[2])
    month = _parse_field(parts[3], *_FIELD_RANGES[3], _MONTH_NAMES)
    dow = _parse_field(parts[4], *_FIELD_RANGES[4], _DOW_NAMES)
    # Normalize Sunday 7 -> 0 for matching against datetime (Mon=0..Sun=6).
    dow = {0 if v == 7 else v for v in dow}
    return {"minute": minute, "hour": hour, "dom": dom,
            "month": month, "dow": dow}


def _matches(parsed: dict, when: datetime) -> bool:
    cron_dow = (when.weekday() + 1) % 7  # Mon=0..Sun=6 -> Sun=0..Sat=6
    return (when.minute in parsed["minute"]
            and when.hour in parsed["hour"]
            and when.day in parsed["dom"]
            and when.month in parsed["month"]
            and cron_dow in parsed["dow"])


def next_run(expr: str, from_dt: datetime | None = None) -> datetime:
    """Next datetime strictly after `from_dt` matching the expression.

    Local time (cron semantics). Raises ValueError on bad expressions.
    """
    parsed = parse_cron(expr)
    cursor = (from_dt or datetime.now()).replace(second=0, microsecond=0)
    cursor += timedelta(minutes=1)
    # 366 days of minutes; per-minute check is cheap, worst case ~0.3s.
    for _ in range(366 * 24 * 60):
        if _matches(parsed, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    raise ValueError(f"no run found within a year for {expr!r}")


def describe(expr: str) -> str:
    """One-line human summary for the UI (best-effort, never raises)."""
    try:
        nxt = next_run(expr)
    except ValueError as exc:
        return f"Invalid schedule: {exc}"
    return f"Next run: {nxt.strftime('%a %Y-%m-%d %H:%M')}"

# ------------------------------------------------------------------ data model


@dataclass
class Schedule:
    id: str = ""
    instance_id: str = ""
    databases: list = field(default_factory=list)
    cron: str = ""
    retention_n: int = 7
    retention_days: int = 0
    dest_dir: str = ""
    enabled: bool = True
    last_run: str = ""
    last_status: str = ""
    created_at: str = ""


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    try:
        dbs = json.loads(row["databases"] or "[]")
    except (ValueError, TypeError):
        dbs = []
    return Schedule(
        id=row["id"], instance_id=row["instance_id"],
        databases=dbs if isinstance(dbs, list) else [],
        cron=row["cron"] or "", retention_n=int(row["retention_n"] or 0),
        retention_days=int(row["retention_days"] or 0),
        dest_dir=row["dest_dir"] or "", enabled=bool(row["enabled"]),
        last_run=row["last_run"] or "", last_status=row["last_status"] or "",
        created_at=row["created_at"] or "")


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SCHEMA)


def _conn(db_path=None) -> sqlite3.Connection:
    conn = _connect(db_path)
    _ensure_table(conn)
    return conn


def _validate(expr: str, databases: list, retention_n: int,
              retention_days: int) -> Result:
    try:
        parse_cron(expr)
    except ValueError as exc:
        return Result.failure(f"Invalid cron expression: {exc}")
    if not databases:
        return Result.failure("Schedule needs at least one database")
    if retention_n < 0 or retention_days < 0:
        return Result.failure("Retention values cannot be negative")
    return Result.success(message="ok")


def create_schedule(instance_id: str, databases: list, cron: str,
                    retention_n: int = 7, retention_days: int = 0,
                    dest_dir: str = "", db_path=None) -> Result:
    """Create + validate a schedule. Never touches the OS scheduler."""
    databases = [str(d) for d in (databases or [])]
    bad = _validate(cron, databases, retention_n, retention_days)
    if not bad.ok:
        return bad
    sched = Schedule(
        id=uuid.uuid4().hex[:12], instance_id=instance_id,
        databases=databases, cron=" ".join(cron.split()),
        retention_n=retention_n, retention_days=retention_days,
        dest_dir=(dest_dir or "").strip(),
        created_at=datetime.now().isoformat(timespec="seconds"))
    try:
        with _conn(db_path) as conn:
            conn.execute(
                "INSERT INTO backup_schedules (id, instance_id, databases,"
                " cron, retention_n, retention_days, dest_dir, enabled,"
                " last_run, last_status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sched.id, sched.instance_id, json.dumps(sched.databases),
                 sched.cron, sched.retention_n, sched.retention_days,
                 sched.dest_dir, 1 if sched.enabled else 0,
                 "", "", sched.created_at))
            conn.commit()
    except OSError as exc:
        return Result.failure(f"Cannot store schedule: {exc}")
    return Result.success(data={"id": sched.id},
                          message=f"Backup schedule created ({sched.cron})")


def list_schedules(instance_id: str | None = None, db_path=None) -> list[Schedule]:
    with _conn(db_path) as conn:
        if instance_id:
            rows = conn.execute(
                "SELECT * FROM backup_schedules WHERE instance_id=?"
                " ORDER BY created_at", (instance_id,))
        else:
            rows = conn.execute(
                "SELECT * FROM backup_schedules ORDER BY created_at")
        return [_row_to_schedule(r) for r in rows]


def get_schedule(schedule_id: str, db_path=None) -> Schedule | None:
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM backup_schedules WHERE id=?",
            (schedule_id,)).fetchone()
    return _row_to_schedule(row) if row else None


def delete_schedule(schedule_id: str, db_path=None) -> Result:
    with _conn(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM backup_schedules WHERE id=?", (schedule_id,))
        conn.commit()
        if cur.rowcount == 0:
            return Result.failure("Schedule not found")
    return Result.success(message="Schedule deleted")


def set_enabled(schedule_id: str, enabled: bool, db_path=None) -> Result:
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE backup_schedules SET enabled=? WHERE id=?",
            (1 if enabled else 0, schedule_id))
        conn.commit()
        if cur.rowcount == 0:
            return Result.failure("Schedule not found")
    return Result.success(
        message=f"Schedule {'enabled' if enabled else 'disabled'}")


def _record_run(schedule_id: str, ok: bool, message: str,
                db_path=None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE backup_schedules SET last_run=?, last_status=?"
            " WHERE id=?", (now, ("ok" if ok else "FAILED: ") + message[:500],
                            schedule_id))
        conn.commit()

# ------------------------------------------------------------------ retention


def _iter_dumps(directory: Path) -> list[dict]:
    """Backup files present: *.dump with mtime/size (+ sidecar if any)."""
    out = []
    if not directory.is_dir():
        return out
    for dump in sorted(directory.glob("*.dump")):
        try:
            st = dump.stat()
        except OSError:
            continue
        meta_path = Path(str(dump) + ".meta.json")
        meta = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                meta = {}
        out.append({"path": str(dump), "mtime": st.st_mtime,
                    "size": st.st_size, "meta": meta})
    return out


def retention_victims(directory: str | Path, retention_n: int,
                      retention_days: int,
                      now: float | None = None) -> list[dict]:
    """Pure selection: which dumps violate the retention policy.

    A dump survives only if within the newest `retention_n` (0 = off) AND
    newer than `retention_days` (0 = off). Never deletes anything itself.
    """
    import time as _time

    now = now if now is not None else _time.time()
    dumps = sorted(_iter_dumps(Path(directory).expanduser()),
                   key=lambda d: d["mtime"], reverse=True)
    victims: list[dict] = []
    for index, dump in enumerate(dumps):
        reasons = []
        if retention_n > 0 and index >= retention_n:
            reasons.append(f"older than newest {retention_n}")
        if retention_days > 0 and dump["mtime"] < now - retention_days * 86400:
            reasons.append(f"older than {retention_days} days")
        if reasons:
            victims.append({**dump, "reason": "; ".join(reasons)})
    return victims


def prune_backups(directory: str | Path, retention_n: int,
                  retention_days: int, audit_tag: str = "scheduled-prune",
                  db_path=None) -> Result:
    """Delete retention victims (+ their sidecars), auditing each deletion.

    Destructive by design — callers must only invoke under an explicit,
    user-configured policy (the schedule's own retention settings).
    """
    from odoo_vite.core import audit as _audit

    victims = retention_victims(directory, retention_n, retention_days)
    deleted, errors = [], []
    for victim in victims:
        ok = True
        for candidate in (victim["path"], victim["path"] + ".meta.json"):
            try:
                Path(candidate).unlink(missing_ok=True)
            except OSError as exc:
                ok = False
                errors.append(f"{candidate}: {exc}")
        _audit.log_event("", "", "backup-prune",
                         f"{audit_tag}: deleted {victim['path']}"
                         f" ({victim['reason']})" if ok else
                         f"{audit_tag}: FAILED to delete {victim['path']}")
        if ok:
            deleted.append(victim["path"])
    if errors and not deleted:
        return Result.failure("; ".join(errors))
    return Result.success(data={"deleted": deleted, "errors": errors},
                          message=f"Pruned {len(deleted)} old backup(s)")

# ------------------------------------------------------------------ execution


def schedule_dest_dir(sched: Schedule, instance_name: str,
                      db_name: str) -> Path:
    """Deterministic per-schedule location (BK.4 lists exactly this)."""
    root = Path((sched.dest_dir or DEFAULT_BACKUPS_ROOT)).expanduser()
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_"
                   for c in instance_name)
    return root / (safe or "unnamed") / db_name


def due_schedules(now: datetime | None = None, db_path=None) -> list[Schedule]:
    """Enabled schedules whose cron matches the current minute.

    Minute granularity: a schedule is due when its expression matches
    `now` truncated to the minute. Callers run at most one backup per
    (schedule, minute) — the runner records last_run to dedupe.
    """
    now = now or datetime.now()
    tick = now.replace(second=0, microsecond=0)
    due = []
    for sched in list_schedules(db_path=db_path):
        if not sched.enabled or not sched.cron:
            continue
        try:
            parsed = parse_cron(sched.cron)
        except ValueError:
            continue
        if _matches(parsed, tick):
            if (sched.last_run or "")[:16] == tick.strftime("%Y-%m-%dT%H:%M"):
                continue  # already ran this minute
            due.append(sched)
    return due


def run_schedule(schedule_id: str, db_path=None) -> Result:
    """Execute one schedule now (runner + UI Run-Now share this)."""
    from odoo_vite.core import db_backup

    sched = get_schedule(schedule_id, db_path)
    if sched is None:
        return Result.failure("Schedule not found")
    inst = get_instance(sched.instance_id, db_path)
    if inst is None:
        msg = "Instance no longer exists — disable or delete this schedule"
        _record_run(schedule_id, False, msg, db_path)
        return Result.failure(msg)
    pw = get_db_password(inst)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    failures, made = [], []
    for db_name in sched.databases:
        dest = schedule_dest_dir(sched, inst.name, db_name) / f"{stamp}.dump"
        res = db_backup.backup_database(
            db_name, dest, db_user=inst.db_user, db_password=pw,
            instance_id=inst.id, instance_name=inst.name)
        if res.ok:
            made.append(str(dest))
        else:
            failures.append(f"{db_name}: {res.message}")
    if sched.retention_n > 0 or sched.retention_days > 0:
        for db_name in sched.databases:
            prune_backups(schedule_dest_dir(sched, inst.name, db_name),
                          sched.retention_n, sched.retention_days,
                          audit_tag=f"schedule:{schedule_id}", db_path=db_path)
    if failures and not made:
        _record_run(schedule_id, False, "; ".join(failures), db_path)
        return Result.failure("; ".join(failures))
    msg = f"Backed up {len(made)} database(s)" + (
        f"; {len(failures)} failed ({'; '.join(failures)})" if failures else "")
    _record_run(schedule_id, True, msg, db_path)
    return Result.success(data={"dumps": made}, message=msg)

# ------------------------------------------------------------------ file index
# BK.4: the sidecar JSON next to each dump IS the index — no second store.
# Only the scheduled-backups root is scanned; manual backups saved by the
# user to arbitrary locations (Sprint 5 file picker) are NOT discoverable
# here — documented limitation, not reconstructed history.


def list_backup_files(instance_name: str | None = None,
                      root: str | Path | None = None) -> list[dict]:
    """Newest-first dump entries under the scheduled-backups root."""
    base = Path(root).expanduser() if root else Path(
        DEFAULT_BACKUPS_ROOT).expanduser()
    if not base.is_dir():
        return []
    out = []
    for dump in sorted(base.rglob("*.dump"),
                       key=lambda p: p.stat().st_mtime
                       if p.exists() else 0, reverse=True):
        try:
            st = dump.stat()
        except OSError:
            continue
        meta: dict = {}
        sidecar = Path(str(dump) + ".meta.json")
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text())
            except (OSError, ValueError):
                meta = {}
        if instance_name and (meta.get("instance_name") or "") != instance_name:
            # Sidecar-less files can't be attributed — show them only in
            # the unfiltered view rather than guessing.
            if meta:
                continue
        out.append({"path": str(dump), "mtime": st.st_mtime,
                    "size": st.st_size, "meta": meta})
    return out


def delete_backup_file(dump_path: str | Path,
                       audit_tag: str = "manual-delete") -> Result:
    """Delete one dump + sidecar with audit. Confirmed by caller first."""
    from odoo_vite.core import audit as _audit

    target = Path(dump_path).expanduser()
    try:
        target.unlink()
        Path(str(target) + ".meta.json").unlink(missing_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot delete {target}: {exc}")
    _audit.log_event("", "", "backup-delete",
                     f"{audit_tag}: deleted {target}")
    return Result.success(message=f"Deleted {target.name}")

# ------------------------------------------------------------------ OS timer
# BK.2: one static minutely systemd USER timer + due-check in the runner.
# Per-schedule units would rot (stale timers for deleted/disabled
# schedules); a single timer has no lifecycle to reconcile — the runner
# evaluates cron expressions itself every minute. User scope, so no
# privilege escalation involved.

TIMER_NAME = "odoo-vite-backup"


def _unit_dir() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".config"
    return base / "systemd" / "user"


def timer_unit_text(python_exe: str | None = None,
                    app_dir: str | None = None) -> tuple[str, str]:
    """(service_text, timer_text). Pure function — unit-tested."""
    import sys as _sys

    exe = python_exe or _sys.executable
    # parents: core/ -> odoo_vite/ -> project root (what `python -m` needs).
    app_dir = app_dir or str(Path(__file__).resolve().parents[2])
    service = (
        "[Unit]\n"
        "Description=Odoo Vite scheduled backups (due-check)\n"
        "After=network-online.target\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={app_dir}\n"
        f"ExecStart={exe} -m odoo_vite.backup_runner --check-due\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Odoo Vite scheduled backups (every minute)\n"
        "[Timer]\n"
        "OnCalendar=minutely\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


def _systemctl(*args: str) -> tuple[int, str]:
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("systemctl") is None:
        return 127, "systemctl not found"
    try:
        proc = _subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True,
            timeout=30)
    except (OSError, _subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def timer_status() -> dict:
    """Installed/enabled/active state of the backup timer (never raises)."""
    info: dict = {"installed": False, "enabled": False, "active": False,
                  "detail": ""}
    service = _unit_dir() / f"{TIMER_NAME}.timer"
    info["installed"] = service.is_file()
    if not info["installed"]:
        return info
    code, _ = _systemctl("is-enabled", f"{TIMER_NAME}.timer")
    info["enabled"] = code == 0
    code, _ = _systemctl("is-active", f"{TIMER_NAME}.timer")
    info["active"] = code == 0
    return info


def install_timer() -> Result:
    """Write units, reload, enable --now. Idempotent."""
    import sys as _sys

    try:
        unit_dir = _unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        service_text, timer_text = timer_unit_text()
        (unit_dir / f"{TIMER_NAME}.service").write_text(service_text)
        (unit_dir / f"{TIMER_NAME}.timer").write_text(timer_text)
    except OSError as exc:
        return Result.failure(f"Cannot write timer units: {exc}")
    code, out = _systemctl("daemon-reload")
    if code not in (0, 127):
        return Result.failure(f"daemon-reload failed: {out.strip()}")
    if code == 127:
        return Result.failure(
            "Units written but systemctl not available — "
            "enable the timer manually once systemd is present")
    code, out = _systemctl("enable", "--now", f"{TIMER_NAME}.timer")
    if code != 0:
        return Result.failure(f"Could not enable timer: {out.strip()}")
    # Refresh ExecStart if the interpreter moved (venv recreated, etc.).
    return Result.success(message="Backup timer installed and running")


def remove_timer() -> Result:
    """Disable + delete units. Idempotent; schedules in the registry stay."""
    code, _ = _systemctl("disable", "--now", f"{TIMER_NAME}.timer")
    removed = []
    for suffix in ("service", "timer"):
        try:
            path = _unit_dir() / f"{TIMER_NAME}.{suffix}"
            if path.is_file():
                path.unlink()
                removed.append(suffix)
        except OSError:
            pass
    _systemctl("daemon-reload")
    if code not in (0, 1, 127):
        return Result.failure("Could not disable timer cleanly")
    return Result.success(
        message="Backup timer removed"
        + (f" ({', '.join(removed)} deleted)" if removed else ""))
