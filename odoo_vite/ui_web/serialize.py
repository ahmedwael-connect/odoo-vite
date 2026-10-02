"""JSON-safe conversion for the web API boundary.

Contract: every value crossing to JavaScript must survive ``json.dumps``.
Result payloads carry Path objects, tuples from core helpers, and
occasionally raw dataclasses — ``to_payload`` normalizes all of them.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from odoo_vite.core.result import Result


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    return str(value)


def to_payload(value: Any) -> Any:
    """Normalize an ops/API return value: Result -> envelope, rest -> jsonable."""
    if isinstance(value, Result):
        return {
            "ok": bool(value.ok),
            "message": str(value.message),
            "data": to_jsonable(value.data),
        }
    return to_jsonable(value)
