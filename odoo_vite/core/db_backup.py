"""Database backup/restore (Sprint 5, Tickets B.5–B.6).

Format decision (locked): custom format (`pg_dump -Fc`) — pg_restore
flexibility later (selective/table-level restores), smaller files, and a
reliable magic header for validation. Plain-SQL dumps are *read* (restore
accepts them) but never *written* by us.

Restore behavior decision: drop-and-recreate the target (clean, predictable —
documented in the UI confirm text and the report). Restoring INTO a live
database as-is risks merged/half-migrated states that are far harder to
reason about than a fresh recreate from the dump.

Metadata: sidecar JSON next to the dump (`<file>.meta.json` — portable,
survives moves, no schema change, visible to the user).

No GTK imports.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from odoo_vite.core.result import Result

CUSTOM_MAGIC = b"PGDMP"
PLAIN_MARKER = b"PostgreSQL database dump"


def validate_dump(dump_path: str | Path) -> Result:
    """Pre-validate a dump file before attempting a restore.

    Custom-format: PGDMP magic, then definitive `pg_restore --list` when the
    binary is available. Plain-SQL: sane header heuristic. Anything else
    (or missing/empty/truncated) fails clearly here — never halfway through
    a restore.
    """
    path = Path(dump_path).expanduser()
    if not path.is_file():
        return Result.failure(f"Dump file not found: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return Result.failure(f"Cannot stat dump file: {exc}")
    if size == 0:
        return Result.failure("Dump file is empty — nothing to restore")
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError as exc:
        return Result.failure(f"Cannot read dump file: {exc}")
    if head.startswith(CUSTOM_MAGIC):
        if size < 100:
            return Result.failure(
                "Dump file has a custom-format header but is suspiciously "
                f"small ({size} bytes) — likely truncated")
        if shutil.which("pg_restore"):
            from odoo_vite.core.proc import run_streaming

            res = run_streaming(["pg_restore", "--list", str(path)],
                                timeout=120)
            if not res.ok:
                return Result.failure(
                    "pg_restore rejects this file "
                    f"({(res.message.splitlines() or ['?'])[0][:160]})")
        return Result.success(data={"format": "custom", "size": size},
                              message="Valid custom-format dump")
    if PLAIN_MARKER in head or head.lstrip().startswith((b"--", b"SET", b"SELECT")):
        return Result.success(data={"format": "plain", "size": size},
                              message="Looks like a plain-SQL dump")
    return Result.failure(
        "File doesn't look like a pg_dump dump (no PGDMP magic, no SQL "
        "dump header) — refusing to restore")


def backup_database(
    db_name: str,
    dest_path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
    db_user: str = "odoo",
    db_password: str | None = None,
    instance_id: str = "",
    instance_name: str = "",
) -> Result:
    """pg_dump -Fc a database + sidecar metadata JSON."""
    from odoo_vite.core.db_manager import is_valid_identifier
    from odoo_vite.core.db_state import get_db_state
    from odoo_vite.core.proc import run_streaming

    if not is_valid_identifier(db_name):
        return Result.failure(f"Invalid database name '{db_name}'")
    if shutil.which("pg_dump") is None:
        return Result.failure("pg_dump not found — install postgresql-client")
    dest = Path(dest_path).expanduser()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot create destination folder: {exc}")
    state = get_db_state(db_name, db_user, db_password)
    if state.error and not state.exists:
        return Result.failure(
            f"Cannot back up '{db_name}': {state.error} "
            "(same lesson as Part A — verify first, don't assume)")
    if not state.exists:
        return Result.failure(f"Database '{db_name}' does not exist — nothing to back up")

    import os

    env = dict(os.environ)
    if db_password:
        env["PGPASSWORD"] = db_password
    res = run_streaming(
        ["pg_dump", "-h", "localhost", "-U", db_user, "-Fc", "-f", str(dest), db_name],
        progress_cb=progress_cb, timeout=3600, env=env,
    )
    if not res.ok:
        return Result.failure(f"pg_dump failed: {res.message}")
    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    meta = {
        "db_name": db_name,
        "db_user": db_user,
        "instance_id": instance_id,
        "instance_name": instance_name,
        "file": str(dest),
        "format": "custom",
        "size_bytes": size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool": "odoo-vite",
    }
    try:
        Path(str(dest) + ".meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
    except OSError as exc:
        return Result.failure(
            f"Backup written but metadata sidecar failed: {exc}")
    return Result.success(
        data={"dest": str(dest), "size_bytes": size, "meta": meta},
        message=f"Backed up '{db_name}' ({size // 1024} KB) to {dest}")


def restore_database(
    dump_path: str | Path,
    target_db: str,
    progress_cb: Callable[[str], None] | None = None,
    db_user: str = "odoo",
    db_password: str | None = None,
) -> Result:
    """Validate → drop-and-recreate target → restore into it.

    Uses create_database (owner fast path, privileged fallback — the H.1
    machinery) so managed roles work here too.
    """
    from odoo_vite.core.db_manager import (
        create_database,
        drop_database,
        is_valid_identifier,
    )
    from odoo_vite.core.db_state import get_db_state
    from odoo_vite.core.proc import run_streaming

    if not is_valid_identifier(target_db):
        return Result.failure(f"Invalid database name '{target_db}'")
    if target_db in ("postgres", "template0", "template1"):
        return Result.failure(f"Refusing to restore over system database '{target_db}'")
    valid = validate_dump(dump_path)
    if not valid.ok:
        return valid
    fmt = (valid.data or {}).get("format", "custom")

    import os

    env = dict(os.environ)
    if db_password:
        env["PGPASSWORD"] = db_password
    state = get_db_state(target_db, db_user, db_password)
    if state.error and not state.exists:
        return Result.failure(
            f"Cannot inspect target '{target_db}': {state.error}")
    if state.exists:
        drop = drop_database(target_db, db_user, db_password)
        if not drop.ok:
            return Result.failure(
                f"Restore aborted: {drop.message} (dump untouched)")
    created = create_database(target_db, db_user, db_password)
    if not created.ok:
        return Result.failure(
            f"Restore aborted: could not recreate '{target_db}': {created.message}")
    if fmt == "custom":
        if shutil.which("pg_restore") is None:
            return Result.failure("pg_restore not found — install postgresql-client")
        cmd = ["pg_restore", "-h", "localhost", "-U", db_user,
               "-d", target_db, str(Path(dump_path).expanduser())]
    else:
        cmd = ["psql", "-h", "localhost", "-U", db_user, "-d", target_db,
               "-v", "ON_ERROR_STOP=1", "-f", str(Path(dump_path).expanduser())]
    res = run_streaming(cmd, progress_cb=progress_cb, timeout=3600, env=env)
    if not res.ok:
        return Result.failure(
            f"Restore into '{target_db}' failed: {res.message} "
            "(target was recreated empty — retry the restore, don't hand-fix)")
    after = get_db_state(target_db, db_user, db_password)
    return Result.success(
        data={"target_db": target_db, "format": fmt,
              "initialized": after.initialized,
              "odoo_version": after.odoo_version},
        message=f"Restored into '{target_db}' from {Path(dump_path).name}")
