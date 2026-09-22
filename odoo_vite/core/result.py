"""Result-style return object (§1.5 of the Phase 1 charter).

Every core/ function that can fail returns a Result instead of raising
raw exceptions into the UI layer, so the UI always has something
user-readable to show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    ok: bool
    message: str = ""
    data: Any = field(default=None)

    @classmethod
    def success(cls, data: Any = None, message: str = "OK") -> "Result":
        return cls(ok=True, message=message, data=data)

    @classmethod
    def failure(cls, message: str, data: Any = None) -> "Result":
        return cls(ok=False, message=message, data=data)

    def __bool__(self) -> bool:
        return self.ok
