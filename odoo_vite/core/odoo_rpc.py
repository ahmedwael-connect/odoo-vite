"""Shared Odoo RPC client (Sprint 10, Ticket 10.1) — the ONE place Part 1
routes through (inspector, records, cron all call this, never raw XML-RPC).

Transport choice: XML-RPC over stdlib xmlrpc.client (documented, stable
across 15.0–19.0, zero new dependencies). JSON-RPC would need `requests`
(a new dependency) for zero capability gain at human-paced admin polling.
Timeouts via a Transport subclass (ServerProxy takes no timeout itself).

Failure mapping (clear Results, never raw tracebacks):
- instance not running / connection refused → "start the instance first"
- unknown/uninitialized DB (checked via db_state BEFORE any RPC) → direct
- Fault "Access Denied" → wrong/stale Odoo credentials (NOT the postgres
  role — a common confusion, said explicitly)
- socket timeout → timeout message with the value used

No GTK imports.
"""

from __future__ import annotations

import socket
import xmlrpc.client
from urllib.error import URLError

from odoo_vite.core.result import Result

RPC_TIMEOUT = 15


class _TimeoutTransport(xmlrpc.client.Transport):
    """http.client connects lazily, so setting .timeout post-construction
    still applies at connect time. Standard recipe."""

    def __init__(self, timeout: int = RPC_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        try:
            conn.timeout = self._timeout
        except Exception:
            pass
        return conn


def _proxies(url: str, timeout: int = RPC_TIMEOUT):
    transport = _TimeoutTransport(timeout)
    common = xmlrpc.client.ServerProxy(
        f"{url}/xmlrpc/2/common", transport=transport, allow_none=True)
    obj = xmlrpc.client.ServerProxy(
        f"{url}/xmlrpc/2/object", transport=transport, allow_none=True,
        use_builtin_types=True)
    return common, obj


def _call(desc: str, func, *args):
    """Run one RPC call, mapping transport/auth faults to messages."""
    try:
        return Result.success(data=func(*args))
    except xmlrpc.client.Fault as exc:
        text = str(exc.faultString or "")
        if "Access Denied" in text or "AccessDenied" in text:
            return Result.failure(
                f"{desc}: Odoo rejected the login — wrong Odoo username or "
                "password (this is the *Odoo* user, not the Postgres role)")
        return Result.failure(f"{desc}: Odoo fault {exc.faultCode}: {text[:800]}")
    except (ConnectionRefusedError, URLError, OSError) as exc:
        return Result.failure(
            f"{desc}: cannot reach the instance ({exc}) — is it running?")
    except (socket.timeout, TimeoutError):
        return Result.failure(
            f"{desc}: timed out after {RPC_TIMEOUT}s — instance overloaded?")
    except Exception as exc:
        return Result.failure(f"{desc}: unexpected error: {exc}")


def server_version(url: str, timeout: int = RPC_TIMEOUT) -> Result:
    """Lightweight probe (no auth): returns version dict or a clear failure."""
    common, _ = _proxies(url, timeout)
    res = _call("version probe", common.version)
    if res.ok and isinstance(res.data, dict):
        return res
    return Result.failure("Instance is not serving XML-RPC (start it first)")


def authenticate(url: str, db: str, user: str, password: str,
                 timeout: int = RPC_TIMEOUT) -> Result:
    """Returns uid (int) or a mapped failure. uid False == bad credentials."""
    if not all([db, user]) or password is None:
        return Result.failure("Database, username and password are all required")
    common, _ = _proxies(url, timeout)
    res = _call("authenticate", common.authenticate, db, user, password, {})
    if not res.ok:
        return res
    if not res.data:
        return Result.failure(
            f"Login failed for Odoo user '{user}' on '{db}' — wrong "
            "username or password (uid came back empty)")
    return Result.success(data={"uid": int(res.data)},
                          message=f"Authenticated as '{user}' (uid {res.data})")


def execute_kw(url: str, db: str, uid: int, password: str, model: str,
               method: str, args=None, kwargs=None,
               timeout: int = RPC_TIMEOUT) -> Result:
    """Generic object call. args=list, kwargs=dict (e.g. fields/offset/limit)."""
    _, obj = _proxies(url, timeout)
    return _call(f"{model}.{method}",
                 obj.execute_kw, db, uid, password, model, method,
                 args or [], kwargs or {})


def search_read(url: str, db: str, uid: int, password: str, model: str,
                domain=None, fields=None, offset: int = 0, limit: int = 50,
                order: str = "") -> Result:
    """Paginated read. Never fetches unbounded (limit required by signature)."""
    kwargs: dict = {"fields": fields or [], "offset": max(0, offset),
                    "limit": max(1, min(int(limit or 50), 200))}
    if order:
        kwargs["order"] = order
    # NOTE: domains pass through untouched. Odoo's canonical form is a list
    # of tuples ([('name', 'ilike', 'x')]); stdlib xmlrpc serializes tuples
    # as arrays and Odoo reads them back as lists. An earlier revision tried
    # to "normalize" tuples to lists here and corrupted valid domains by
    # adding a nesting level — reverted, with a test pinning pass-through.
    res = execute_kw(url, db, uid, password, model, "search_read",
                     [domain or []], kwargs)
    if res.ok and not isinstance(res.data, list):
        return Result.failure(f"{model}.search_read returned non-list data")
    return res


def connect_instance(  # type: ignore[no-untyped-def]
    instance, db: str | None = None,
    odoo_user: str | None = None, odoo_password: str | None = None,
    db_path=None, timeout: int = RPC_TIMEOUT) -> Result:
    """Full connect: running? → initialized? → authenticate?

    Returns data={url, db, uid, user, password} for subsequent calls.
    Missing Odoo credentials are a clean failure (the UI prompts) — never
    guessed, never silently defaulted.
    """
    from odoo_vite.core.db_state import get_db_state
    from odoo_vite.core.process_manager import _alive_pid
    from odoo_vite.core.registry import get_db_password

    if _alive_pid(instance) is None:
        return Result.failure(
            f"Instance '{instance.name}' is not running — start it first; "
            "RPC introspection needs a live server")
    target = ((db or instance.primary_db) or "").strip()
    if not target:
        return Result.failure("No database selected")
    try:
        pw = get_db_password(instance) or None
    except Exception:
        pw = None
    state = get_db_state(target, instance.db_user or "odoo", pw)
    if state.error and not state.exists:
        return Result.failure(f"Cannot inspect '{target}': {state.error}")
    if not state.exists:
        return Result.failure(f"Database '{target}' does not exist")
    if not state.initialized:
        return Result.failure(
            f"Database '{target}' is not initialized — run Initialize first")
    if not odoo_user:
        return Result.failure(
            "Odoo username required (enter it in the Dev Tools tab — "
            "this is the Odoo login, e.g. admin, not the Postgres role)",
            data={"needs_credentials": True, "db": target})
    url = f"http://localhost:{instance.port}"
    auth = authenticate(url, target, odoo_user, odoo_password or "",
                        timeout=timeout)
    if not auth.ok:
        return auth
    return Result.success(
        data={"url": url, "db": target, "uid": auth.data["uid"],
              "user": odoo_user, "password": odoo_password or ""},
        message=f"Connected to '{target}' as '{odoo_user}'")


def remember_credentials(instance_id: str, user: str, password: str) -> None:
    """Persist Odoo login in the OS keyring (best-effort, never raises)."""
    try:
        import keyring

        keyring.set_password("odoo-vite", f"{instance_id}:rpc",
                             f"{user}\x1f{password}")
    except Exception:
        pass


def recall_credentials(instance_id: str) -> tuple[str, str]:
    """Return (user, password) from keyring, or ("", "") if absent."""
    try:
        import keyring

        raw = keyring.get_password("odoo-vite", f"{instance_id}:rpc") or ""
        if "\x1f" in raw:
            user, _, password = raw.partition("\x1f")
            return user, password
    except Exception:
        pass
    return "", ""
