"""Simple pub/sub event bus with a GLib.idle_add bridge to the UI.

Zero hard GTK dependency: GLib is imported lazily and only used when
available (i.e. inside the running desktop app). Under pytest (no GTK
main loop) callbacks fire synchronously, which keeps core/ testable.
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
    """Emit an event on the GTK main thread when possible, else synchronously.

    UI code should prefer this when emitting from background threads.
    """
    try:
        from gi.repository import GLib  # type: ignore

        def _dispatch() -> bool:
            emit(event, data)
            return False  # one-shot idle callback

        GLib.idle_add(_dispatch)
    except Exception:
        emit(event, data)


def clear_all() -> None:
    """Remove all subscriptions (mainly for tests)."""
    _subscribers.clear()
