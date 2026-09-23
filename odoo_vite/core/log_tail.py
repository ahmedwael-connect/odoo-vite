"""Seek-based log tailing (Sprint 8, Ticket B.1).

Tracks the last-read byte offset per file: each poll seeks there and reads
only new bytes — never re-reads from the start. Rotation/truncation (file
smaller than the known offset, or inode change) resets to 0 with an explicit
flag so the UI can note it instead of showing garbage. No GTK imports.
"""

from __future__ import annotations

import os


class LogFollower:
    """Stateful follower for one log file. Not thread-safe by itself —
    drive it from a single poll loop (the UI timer owns one per instance)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.offset = 0
        self._partial = ""

    def poll(self, max_bytes: int = 256 * 1024) -> dict:
        """Read new bytes since last poll.

        Returns {"lines", "rotated", "missing"}. Lines are complete only
        (a trailing partial line is held for the next poll).
        """
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return {"lines": [], "rotated": False, "missing": True}
        if size < self.offset:
            self.offset = 0
            self._partial = ""
            rotated = True
        else:
            rotated = False
        if size == self.offset:
            return {"lines": [], "rotated": rotated, "missing": False}
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(min(max_bytes, size - self.offset))
                self.offset += len(chunk)
        except OSError:
            return {"lines": [], "rotated": rotated, "missing": True}
        text = self._partial + chunk.decode("utf-8", errors="replace")
        if text.endswith("\n"):
            self._partial = ""
            lines = text.splitlines()
        else:
            *lines, self._partial = text.split("\n")
        return {"lines": lines, "rotated": rotated, "missing": False}

    def reset(self) -> None:
        self.offset = 0
        self._partial = ""

    def sync_to_end(self) -> None:
        """Fast-forward to EOF (use after an initial read_last_n fill so the
        first poll doesn't re-emit what was just loaded)."""
        try:
            self.offset = os.path.getsize(self.path)
        except OSError:
            self.offset = 0
        self._partial = ""


def read_last_n(path: str, n: int = 2000, chunk: int = 65536) -> list[str]:
    """Efficient tail: last n lines without reading the whole file."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size == 0:
        return []
    data = b""
    with open(path, "rb") as fh:
        pos = size
        while pos > 0 and data.count(b"\n") <= n:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]
