"""Append-only audit log (Sprint 3, Ticket 3.8).

One JSON line per lifecycle event at ~/.local/share/odoo-vite/audit.log:
{"ts", "instance_id", "instance_name", "action", "detail"}.
Actions: start | stop | restart | db_create | forced_kill | process_gone.
Dead simple by design — no rotation yet (volume is a few lines per user
action; rotation can come if the file ever matters).

Honours ODOO_VITE_AUDIT env override (tests). Never raises. No GTK imports.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
