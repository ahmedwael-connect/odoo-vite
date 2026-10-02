"""Push channel: Python -> browser events (the queue-drain replacement).

Every event is an envelope ``{"kind": str, "payload": dict}``. Kind names
match the old ``bridge._drain`` taxonomy one-for-one (message, refresh,
db-states, modules-ready, progress-line, ...) so the React side ports the
same routing table.

Transports:
- production: evaluate_js -> ``window.odooVite.push(envelope)``
- tests: ``ListTransport`` collecting envelopes for assertions

A failing transport never breaks the operation that emitted the event —
pushes happen from worker threads while the window may be tearing down.
"""

from __future__ import annotations

from typing import Callable

from odoo_vite.ui_web.serialize import to_jsonable

Transport = Callable[[dict], None]


class PushChannel:
    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport

    def set_transport(self, transport: Transport) -> None:
        self._transport = transport

    def emit(self, kind: str, payload: dict | None = None) -> None:
        if self._transport is None:
            return
        envelope = {"kind": kind, "payload": to_jsonable(payload or {})}
        try:
            self._transport(envelope)
        except Exception:
            pass


class ListTransport:
    """Collects envelopes for assertions."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, envelope: dict) -> None:
        self.events.append(envelope)

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]

    def last(self, kind: str) -> dict | None:
        for envelope in reversed(self.events):
            if envelope["kind"] == kind:
                return envelope
        return None
