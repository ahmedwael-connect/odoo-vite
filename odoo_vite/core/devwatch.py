"""Dev-mode file watching, core half (Sprint 11, Ticket 11.2).

Mechanism decision: the observer lives outside core — Qt used
QFileSystemWatcher in its UI layer; the web app uses watchdog's
inotify observer (ops/devwatch.py), per the spec's "real filesystem-
watching mechanism, not polling". This module is the pure, testable
half: WHICH paths matter + the debounce controller.

Debounce: first event arms a quiet window (default 0.8s); every further
event re-arms; exactly one restart fires after quiet. Same batching spirit
as the Sprint 8 event-panel debounce, adapted to restart semantics.
Clock injectable for deterministic tests. No GTK imports.
"""

from __future__ import annotations

import time
from pathlib import Path

QUIET_SECONDS = 0.8
WATCHED_SUFFIXES = (".py", ".xml", ".js", ".css", ".csv", ".po", ".pot")
# Well-known junk dirs skipped anywhere in the path. NOTE: plain dotfile
# PARENTS are deliberately NOT skipped — every Linux home contains .local,
# .config, etc., and policing parents broke all real paths (found live in
# Sprint 11: ~/.local/... was rejected). Only dot-LEAF names are skipped.
SKIP_DIRS = {"__pycache__", ".git", ".hg", ".svn", ".tox", ".venv",
             "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             ".idea", ".vscode"}


def should_watch(path: str) -> bool:
    """True for editable source files Odoo would re-read on restart."""
    try:
        parts = Path(path).parts
    except Exception:
        return False
    if not parts:
        return False
    if parts[-1].startswith("."):
        return False
    if any(part in SKIP_DIRS for part in parts):
        return False
    return str(path).lower().endswith(WATCHED_SUFFIXES)


def watch_roots(instance) -> list[str]:  # type: ignore[no-untyped-def]
    """Scope: custom_addons always; community/enterprise too (they change
    less often, but a git pull there deserves the same restart).

    custom_addons_path may be a comma-separated list (standard Odoo
    conf form: ``addons_path = a/b, c/d``) — each entry is checked
    separately; a whole unsplit string never matches ``is_dir()``.
    """
    roots: list[str] = []
    raw_custom = str(instance.custom_addons_path or "")
    candidates = [p.strip() for p in raw_custom.split(",") if p.strip()]
    candidates += [instance.community_path or "", instance.enterprise_path or ""]
    for raw in candidates:
        if raw and Path(raw).is_dir() and raw not in roots:
            roots.append(raw)
    return roots


class DebounceController:
    """Feed filesystem events; exactly one on_quiet() per burst.

    on_quiet is invoked synchronously from check() when the quiet window
    elapses — the UI layer calls check() from a QTimer tick (or tests drive
    it with a fake clock).
    """

    def __init__(self, quiet_seconds: float = QUIET_SECONDS,
                 clock=time.monotonic) -> None:
        self.quiet = quiet_seconds
        self._clock = clock
        self._armed_at: float | None = None
        self._pending = 0
        self.fires = 0

    def feed(self) -> None:
        now = self._clock()
        if self._armed_at is None:
            self._armed_at = now
        else:
            # re-arm: burst continues, single restart still pending
            self._armed_at = now
        self._pending += 1

    def check(self, on_quiet) -> bool:
        """Returns True if a restart fired (on_quiet called once)."""
        if self._armed_at is None:
            return False
        if self._clock() - self._armed_at >= self.quiet:
            self._armed_at = None
            self._pending = 0
            self.fires += 1
            on_quiet()
            return True
        return False

    @property
    def pending(self) -> bool:
        return self._armed_at is not None
