"""Disk usage measurement (U5.3): du for instance folders.

Pure stdlib, never raises — the UI runs it in a worker (large trees take
seconds) and renders Result.data. No GUI imports.
"""

from __future__ import annotations

import os
from pathlib import Path

from odoo_vite.core.result import Result


def measure_dir(path: str | Path) -> int:
    """Recursive size in bytes (no symlink following — avoids cycles and
    double-counting venv links). Unreadable entries are skipped."""
    total = 0
    try:
        stack = [Path(path).expanduser()]
    except Exception:
        return 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def measure_instance(instance) -> Result:  # type: ignore[no-untyped-def]
    """Break down an instance's folder (total + code/venv/addons/logs)."""
    try:
        base = Path(instance.path).expanduser() if instance.path else None
        if base is None or not base.is_dir():
            return Result.failure(
                f"No files to measure for '{instance.name}' "
                f"({instance.path or 'no path'})")

        def _part(raw: str | None) -> int:
            if not raw:
                return 0
            p = Path(raw).expanduser()
            if not str(p).startswith(str(base)):
                return 0  # external (adopted) paths are not the clone's cost
            if p.is_file():
                try:
                    return p.stat().st_size
                except OSError:
                    return 0
            return measure_dir(p) if p.is_dir() else 0

        parts = {
            "code": _part(instance.community_path),
            "venv": _part(instance.venv_path),
            "custom_addons": _part(instance.custom_addons_path),
            "logs": _part(str(base / "logs")),
        }
        total = measure_dir(base)
        parts["other"] = max(0, total - sum(parts.values()))
        try:
            from odoo_vite.core.db_state import human_size
            human = human_size(total)
        except Exception:
            human = f"{total} bytes"
        return Result.success(
            data={"total_bytes": total, "parts": parts, "human": human},
            message=f"Disk usage: {human}")
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"Disk measurement failed: {exc}")
