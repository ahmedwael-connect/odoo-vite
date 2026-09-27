"""Simple pub/sub event bus (framework-agnostic).

Zero GUI dependencies: this module imports only stdlib. Delivery to the
UI thread is the frontend's job — the Qt frontend does it with queued
signals in odoo_vite.ui_qt.workers (run_in_background). Under pytest
callbacks fire synchronously, which keeps core/ testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

_subscribers: dict[str, list[Callable[..., None]]] = defaultdict(list)


def subscribe(event: str, callback: Callable[..., None]) -> Callable[[], None]:
    """Subscribe to an event. Returns an unsubscribe callable."""
    _subscribers[event].append(callback)

    def _unsubscribe() -> None:
        try:
            _subscribers[event].remove(callback)
        except ValueError:
            pass

    return _unsubscribe


def emit(event: str, data: Any = None) -> None:
    """Emit an event synchronously (safe to call from any thread for pure-core use)."""
    for callback in list(_subscribers.get(event, [])):
        callback(data)


def emit_ui(event: str, data: Any = None) -> None:
    """Deprecated alias of emit() (U1.1: legacy toolkit bridge removed).

    Kept so old callers keep working; new code should use emit() in
    core and let the Qt layer marshal via queued signals. Will be
    removed in a future release.
    """
    emit(event, data)


def clear_all() -> None:
    """Remove all subscriptions (mainly for tests)."""
    _subscribers.clear()
