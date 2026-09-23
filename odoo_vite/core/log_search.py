"""Log search over full files (Sprint 8, Ticket B.2).

Streams the file in fixed-size text chunks (never loads it whole), with a
carry-over for split lines. Supports regex, Odoo level filter, timestamp
range, and grep -C style context. No GTK imports.
"""

from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime

from odoo_vite.core.result import Result

CHUNK = 65536
MAX_MATCHES = 2000

# e.g. "2026-09-22 09:11:44,120 34077 INFO e2e_17_demo odoo.modules.loading: ..."
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s+"
    r"(?P<pid>\d+)\s+(?P<level>[A-Z]+)\s+(?P<rest>.*)$")
TS_FORMATS = ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S")


def parse_level(line: str) -> str | None:
    match = LINE_RE.match(line)
    return match.group("level") if match else None


def parse_timestamp(line: str) -> datetime | None:
    match = LINE_RE.match(line)
    if not match:
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(match.group("ts"), fmt)
        except ValueError:
            continue
    return None


def _iter_lines(path: str, chunk: int = CHUNK):
    """Yield (lineno, line) streaming. 1-indexed linenos."""
    lineno = 0
    carry = ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            text = carry + block
            *complete, carry = text.split("\n")
            for line in complete:
                lineno += 1
                yield lineno, line
    if carry:
        lineno += 1
        yield lineno, carry


def search_file(path: str, pattern: str, level: str | None = None,
                since: datetime | None = None, until: datetime | None = None,
                context: int = 2, chunk: int = CHUNK,
                max_matches: int = MAX_MATCHES) -> Result:
    """Search a log file. data={matches: [{lineno, line, before[], after[]}],
    truncated, scanned_lines}."""
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return Result.failure(f"Invalid regex '{pattern}': {exc}")
    if not os.path.isfile(path):
        return Result.failure(f"Log file not found: {path}")
    if level:
        level = level.upper()

    matches: list[dict] = []
    before: deque[str] = deque(maxlen=max(0, context))
    pending: list[dict] = []  # matches still collecting after-context
    remaining_after: dict[int, int] = {}
    scanned = 0
    truncated = False
    try:
        for lineno, line in _iter_lines(path, chunk):
            scanned += 1
            # feed after-context to pending matches first
            for m in pending:
                if remaining_after.get(id(m), 0) > 0:
                    m["after"].append(line)
                    remaining_after[id(m)] -= 1
            pending = [m for m in pending if remaining_after.get(id(m), 0) > 0]

            hit = rx.search(line) is not None
            if hit and level and (parse_level(line) or "") != level:
                hit = False
            if hit and (since or until):
                ts = parse_timestamp(line)
                if ts is None:
                    hit = False  # can't place it in range: exclude
                else:
                    if since and ts < since:
                        hit = False
                    if until and ts > until:
                        hit = False
            if hit:
                if len(matches) >= max_matches:
                    truncated = True
                    break
                match = {"lineno": lineno, "line": line,
                         "before": list(before), "after": []}
                matches.append(match)
                if context > 0:
                    pending.append(match)
                    remaining_after[id(match)] = context
            before.append(line)
    except OSError as exc:
        return Result.failure(f"Cannot read log file: {exc}")
    return Result.success(
        data={"matches": matches, "truncated": truncated,
              "scanned_lines": scanned},
        message=(f"{len(matches)} match(es)"
                 + (" (truncated at %d)" % max_matches if truncated else "")))
