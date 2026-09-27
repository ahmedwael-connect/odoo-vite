"""Append-only audit log (Sprint 3, Ticket 3.8; rotation U4.1).

One JSON line per lifecycle event at ~/.local/share/odoo-vite/audit.log:
{"ts", "instance_id", "instance_name", "action", "detail"}.
Actions: start | stop | restart | db_create | forced_kill | process_gone.

Rotation: when the file exceeds MAX_BYTES, it shifts to audit.log.1
(.1 -> .2 -> .3, oldest dropped) and a fresh file starts. Keeps the
event dock + read_events cheap even with scheduled-backup volume.

Honours ODOO_VITE_AUDIT env override (tests). Never raises. No GUI imports.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
KEEP_ROTATED = 3


def audit_path() -> Path:
    override = os.environ.get("ODOO_VITE_AUDIT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "odoo-vite" / "audit.log"


def log_event(
    instance_id: str,
    instance_name: str,
    action: str,
    detail: str = "",
) -> None:
    """Append one audit event. Best-effort — never raises."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "instance_id": instance_id,
        "instance_name": instance_name,
        "action": action,
        "detail": detail,
    }
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        _maybe_rotate(path)
    except OSError:
        pass


def _maybe_rotate(path: Path) -> None:
    """Shift audit.log -> .1 -> .2 -> .3 when over MAX_BYTES. Never raises."""
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
        for i in range(KEEP_ROTATED, 0, -1):
            src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
            dst = path.with_name(f"{path.name}.{i}")
            try:
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            except OSError:
                return
    except OSError:
        pass


def read_events(limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent audit events (newest last). Never raises."""
    try:
        lines = audit_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events
