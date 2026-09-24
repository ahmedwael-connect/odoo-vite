"""Headless scheduled-backup runner (Sprint BK.2).

Invoked by the systemd user timer once a minute — never by the GUI
(the UI calls backup_scheduler.run_schedule() in-process for Run-Now).
No GTK imports anywhere in this path (core only), so it runs without a
display. Overlapping runs are serialized via a lock file; a run already
in progress makes a new invocation exit 0 ("skip", not failure).

Usage:
    python -m odoo_vite.backup_runner --check-due
    python -m odoo_vite.backup_runner --schedule-id <id>
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

from odoo_vite.core import backup_scheduler
from odoo_vite.core.audit import log_event


def _lock_path() -> Path:
    return Path.home() / ".local" / "share" / "odoo-vite" / "backup-runner.lock"


def check_due() -> int:
    """Run every due schedule once. Returns process exit code."""
    lock_file = _lock_path()
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_file, "w")
    except OSError as exc:
        print(f"backup-runner: cannot open lock file: {exc}", file=sys.stderr)
        return 1
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("backup-runner: previous run still in progress, skipping")
        return 0
    try:
        due = backup_scheduler.due_schedules()
    except Exception as exc:
        print(f"backup-runner: cannot list schedules: {exc}", file=sys.stderr)
        return 1
    if not due:
        return 0
    failed = 0
    for sched in due:
        try:
            res = backup_scheduler.run_schedule(sched.id)
        except Exception as exc:  # never let one schedule kill the sweep
            res = None
            print(f"backup-runner: schedule {sched.id} raised: {exc}",
                  file=sys.stderr)
        ok = bool(res and res.ok)
        print(f"backup-runner: schedule {sched.id} "
              f"({'ok' if ok else 'FAILED: ' + (res.message if res else '?')})")
        log_event(sched.instance_id, "",
                  "scheduled-backup",
                  f"schedule {sched.id}: "
                  f"{res.message if res else 'raised, see stderr'}")
        if not ok:
            failed += 1
    return 1 if failed else 0


def run_one(schedule_id: str) -> int:
    """Run a single schedule (manual/CLI use, debugging)."""
    try:
        res = backup_scheduler.run_schedule(schedule_id)
    except Exception as exc:
        print(f"backup-runner: {exc}", file=sys.stderr)
        return 1
    print(f"backup-runner: {res.message}")
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Odoo Vite headless scheduled-backup runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-due", action="store_true",
                       help="run every schedule due this minute")
    group.add_argument("--schedule-id", metavar="ID",
                       help="run one schedule by id")
    args = parser.parse_args(argv)
    if args.check_due:
        return check_due()
    return run_one(args.schedule_id)


if __name__ == "__main__":
    sys.exit(main())
