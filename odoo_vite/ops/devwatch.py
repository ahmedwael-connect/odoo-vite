"""Dev Mode Watch ops (Sprint 11, Ticket 11.2): sessions + wiring.

Mechanism decision (spec requires a real FS-watch, no polling):
**watchdog's inotify Observer** — event-driven, GUI-free (fits the ops
layer, unlike Gio), well-supported, and testable in CI without a
display. Chosen over Gio.FileMonitor (GUI toolkit would break the
ops layering) and polling (explicitly banned by the spec).

Division of labour:
- ``core/devwatch.py`` — pure scope + debounce: ``should_watch``,
  ``watch_roots``, ``DebounceController`` (unit-tested in test_sprint11).
- here — session lifecycle: observer thread + a debounce tick thread,
  exactly one restart per burst through the injected ``restart_cb``
  (facade wires ``LifecycleOps.restart``), and ``on_event`` payloads
  that the facade pushes as ``dev-watch`` events.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from odoo_vite.core import devwatch as devwatch_core
from odoo_vite.core import process_manager
from odoo_vite.core.registry import get_instance
from odoo_vite.core.result import Result

TICK_SECONDS = 0.1
# Real mutations only. Reads emit opened/closed_no_write — Odoo's startup
# imports every .py in the watched roots, and feeding those would arm the
# debounce on every restart (an endless restart loop, found live in e2e).
MUTATING_EVENT_TYPES = frozenset({"created", "modified", "deleted", "moved"})


def _running(instance_id: str) -> bool:
    """Fresh running check via the batch status poll (self-healing)."""
    try:
        for row in process_manager.get_statuses():
            if row.get("id") == instance_id:
                return row.get("status") == "running"
    except Exception:
        return False
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, feed: Callable[[str], None]) -> None:
        super().__init__()
        self._feed = feed

    def on_any_event(self, event) -> None:  # noqa: D102 — watchdog API
        if getattr(event, "is_directory", False):
            return
        if getattr(event, "event_type", "") not in MUTATING_EVENT_TYPES:
            return  # opened/closed_no_write: reads must never arm the debounce
        path = getattr(event, "dest_path", "") or getattr(event, "src_path", "") or ""
        if path:
            self._feed(str(path))


class _Session:
    """One watched instance: observer + debounce tick thread."""

    def __init__(
        self,
        instance_id: str,
        roots: list[str],
        restart_cb: Callable | None,
        emit: Callable[..., None],
        clock: Callable[[], float],
        quiet_seconds: float,
    ) -> None:
        self.instance_id = instance_id
        self.roots = roots
        self.restart_cb = restart_cb
        self._emit = emit
        self.controller = devwatch_core.DebounceController(
            quiet_seconds=quiet_seconds, clock=clock
        )
        self.observer = Observer()
        self.observer.daemon = True  # never block interpreter exit
        self.stop_evt = threading.Event()
        self.ticker = threading.Thread(
            target=self._tick, daemon=True, name=f"devwatch-{instance_id[:8]}"
        )
        self.changes = 0
        self.fires = 0
        self.started = False

    def start(self) -> None:
        handler = _Handler(self.feed)
        for root in self.roots:
            self.observer.schedule(handler, root, recursive=True)
        self.observer.start()
        self.ticker.start()
        self.started = True

    def feed(self, path: str) -> None:
        """Observer-thread entry: filter, feed the debounce, announce arm."""
        if not devwatch_core.should_watch(path):
            return
        was_pending = self.controller.pending
        self.controller.feed()
        if not was_pending:
            self.changes += 1
            name = Path(path).name
            self._emit(
                self.instance_id,
                "change",
                f"Change detected ({name}) — restarting once edits settle",
                self,
            )

    def _tick(self) -> None:
        while not self.stop_evt.is_set():
            time.sleep(TICK_SECONDS)
            try:
                self.controller.check(self._on_quiet)
            except Exception as exc:  # never kill the watcher thread
                self._emit(self.instance_id, "error", str(exc), self)

    def _on_quiet(self) -> None:
        """Debounce fired: exactly one restart attempt per burst."""
        self.fires += 1
        if not _running(self.instance_id):
            self._emit(
                self.instance_id,
                "skipped",
                "Change settled, but the instance is not running — not restarting",
                self,
            )
            return
        self._emit(
            self.instance_id,
            "restarting",
            f"Source changed — restarting (burst #{self.fires})",
            self,
        )
        if self.restart_cb is None:
            self._emit(self.instance_id, "error", "No restart callback wired", self)
            return
        try:
            res = asyncio.run(self.restart_cb(self.instance_id))
        except Exception as exc:
            self._emit(self.instance_id, "error", f"Restart failed: {exc}", self)
            return
        ok = bool(getattr(res, "ok", False))
        message = str(getattr(res, "message", res))
        self._emit(
            self.instance_id, "restarted" if ok else "error", message, self
        )

    def shutdown(self) -> None:
        self.stop_evt.set()
        if self.started:
            self.observer.stop()
            self.observer.join(timeout=5)
        self.ticker.join(timeout=2)


class DevWatchOps:
    """Per-instance watch sessions: start/stop/status + event fan-out."""

    def __init__(
        self,
        on_event: Callable[[dict], None] | None = None,
        restart_cb: Callable | None = None,
        clock: Callable[[], float] = time.monotonic,
        quiet_seconds: float = devwatch_core.QUIET_SECONDS,
    ) -> None:
        self._event = on_event or (lambda _payload: None)
        self._restart_cb = restart_cb
        self._clock = clock
        self._quiet = quiet_seconds
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _emit(self, instance_id: str, state: str, message: str,
              session: _Session | None = None) -> None:
        try:
            self._event(
                {
                    "instance_id": instance_id,
                    "state": state,
                    "message": message,
                    "changes": session.changes if session else 0,
                    "fires": session.fires if session else 0,
                }
            )
        except Exception:
            pass  # a dead push channel must not kill a watcher

    def start(self, instance_id: str) -> Result:
        inst = get_instance(instance_id)
        if inst is None:
            return Result.failure(f"No instance with id '{instance_id}'")
        roots = devwatch_core.watch_roots(inst)
        if not roots:
            return Result.failure(
                "Nothing to watch — no addons roots exist on disk yet"
            )
        with self._lock:
            if instance_id in self._sessions:
                return Result.failure(
                    "Watch is already running for this instance"
                )
            session = _Session(
                instance_id,
                roots,
                self._restart_cb,
                self._emit,
                self._clock,
                self._quiet,
            )
            try:
                session.start()
            except Exception as exc:
                return Result.failure(f"Cannot start watcher: {exc}")
            self._sessions[instance_id] = session
        self._emit(
            instance_id,
            "started",
            f"Watching {len(roots)} root(s) — one restart per change burst",
            session,
        )
        return Result.success(
            data=self.status(instance_id),
            message=f"Watch started ({len(roots)} root(s))",
        )

    def stop(self, instance_id: str) -> Result:
        with self._lock:
            session = self._sessions.pop(instance_id, None)
        if session is None:
            return Result.failure("Watch is not running for this instance")
        try:
            session.shutdown()
        except Exception:
            pass
        self._emit(instance_id, "stopped", "Watch stopped", session)
        return Result.success(data=self.status(instance_id), message="Watch stopped")

    def status(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(instance_id)
        if session is None:
            return {"watching": False, "roots": [], "changes": 0, "fires": 0}
        return {
            "watching": True,
            "roots": list(session.roots),
            "changes": session.changes,
            "fires": session.fires,
        }

    def stop_all(self) -> None:
        """Shut every session down (app exit / tests)."""
        with self._lock:
            ids = list(self._sessions)
        for instance_id in ids:
            self.stop(instance_id)
