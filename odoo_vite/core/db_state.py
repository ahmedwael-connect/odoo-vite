"""Single source of truth for Postgres database state (Sprint 5, Part A).

Every feature that asks "does this DB exist / is it initialized" comes
here — Start's confirm/collision logic, Switch, Remove's drop gate, Adopt's
db_created probe, Discover grouping, the Databases tab. One implementation,
no per-caller re-derivation (that's the bug class this module retires).

Deliberately cache-free: always asks Postgres directly. Callers that poll
on a loop cache at the call site with an explicit TTL.

No GTK imports.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


@dataclass
class DbState:
    db_name: str
    exists: bool = False
    initialized: bool = False  # ir_module_module present with base installed
    odoo_version: str | None = None  # e.g. "17.0.1.3" (major via enterprise_major)
    size_bytes: int | None = None
    owner: str | None = None
    error: str | None = None  # set when Postgres couldn't be asked at all


def _qliteral(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(cmd: list[str], env_extra: dict[str, str] | None = None,
         timeout: int = 30) -> tuple[int, str]:
    import shutil

    if not shutil.which(cmd[0]):
        return -1, f"{cmd[0]} not found"
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    except OSError as exc:
        return -1, f"cannot start {cmd[0]}: {exc}"


def get_db_state(db_name: str, db_user: str = "odoo",
                 db_password: str | None = None) -> DbState:
    """Ask Postgres for everything we track about one database, in one go."""
    from odoo_vite.core.db_manager import is_valid_identifier

    state = DbState(db_name=db_name)
    if not is_valid_identifier(db_name):
        state.error = f"invalid database name '{db_name}'"
        return state
    extra = {"PGPASSWORD": db_password} if db_password else None

    def _q(db: str, sql: str) -> tuple[int, str]:
        return _run(["psql", "-h", "localhost", "-U", db_user, "-d", db,
                     "-tAc", sql], env_extra=extra)

    rc, out = _q("postgres",
                 "SELECT datname, pg_get_userbyid(datdba), pg_database_size(datname)"
                 f" FROM pg_database WHERE datname = {_qliteral(db_name)}")
    if rc != 0:
        state.error = (out.splitlines() or ["postgres unreachable"])[0][:200]
        return state
    parts = out.split("|")
    if not parts or not parts[0].strip():
        return state  # exists=False; initialized False; nothing more to ask
    state.exists = True
    state.owner = parts[1].strip() if len(parts) > 1 else None
    try:
        state.size_bytes = int(parts[2].strip()) if len(parts) > 2 else None
    except ValueError:
        state.size_bytes = None

    rc, out = _q(db_name,
                 "SELECT latest_version FROM ir_module_module "
                 "WHERE name = 'base' AND state = 'installed'")
    if rc != 0:
        return state  # exists but uninitialized (or unconnectable as this user)
    version = out.strip().splitlines()
    state.initialized = bool(version and version[0].strip())
    state.odoo_version = version[0].strip() if state.initialized else None
    return state


def odoo_major(version: str | None) -> str:
    """'17.0.1.3' → '17.0' ('' when unknown)."""
    match = re.match(r"\s*(\d+\.\d+)", str(version or ""))
    return match.group(1) if match else ""


def validate_db_config(instance) -> dict:  # type: ignore[no-untyped-def]
    """Compare an instance's odoo.conf db_* settings against live Postgres.

    Returns {"checks": [{field, ok, detail}]} — ok True/False, or None when
    the check couldn't run (e.g. later checks depend on an earlier failure).
    Never raises. Passwords never appear in details.
    """
    import configparser
    import socket

    checks: list[dict] = []

    def _add(field: str, ok: bool | None, detail: str) -> None:
        checks.append({"field": field, "ok": ok, "detail": detail})

    conf_path = (instance.conf_path or "").strip()
    if not conf_path:
        _add("conf_file", False, "instance records no odoo.conf path")
        return {"checks": checks}
    try:
        parser = configparser.RawConfigParser()
        parser.read(conf_path, encoding="utf-8")
        opts = dict(parser.items("options")) if parser.has_section("options") else {}
    except Exception as exc:
        _add("conf_file", False, f"cannot parse {conf_path}: {exc}")
        return {"checks": checks}
    _add("conf_file", True, conf_path)

    host = opts.get("db_host", "") or "localhost"
    try:
        port = int(str(opts.get("db_port", "5432")).strip() or "5432")
    except ValueError:
        _add("db_port", False, f"not a number: {opts.get('db_port')!r}")
        return {"checks": checks}
    try:
        with socket.create_connection((host, port), timeout=5):
            _add("db_host/db_port", True, f"{host}:{port} reachable")
            reachable = True
    except OSError as exc:
        _add("db_host/db_port", False, f"{host}:{port} unreachable ({exc})")
        reachable = False
    if not reachable:
        for field in ("db_user", "db_password", "primary_db", "can_create"):
            _add(field, None, "skipped — server unreachable")
        return {"checks": checks}

    user = opts.get("db_user", "") or instance.db_user or "odoo"
    from odoo_vite.core.registry import get_db_password

    try:
        password = get_db_password(instance)
    except Exception:
        password = ""
    rc, out = _run(
        ["psql", "-h", host, "-U", user, "-d", "postgres",
         "-tAc", "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"],
        env_extra={"PGPASSWORD": password} if password else None,
        timeout=30)
    low = out.lower()
    if rc == 0:
        _add("db_user", True, f"role '{user}' exists")
        _add("db_password", True, "password accepted")
        _add("can_create", True if out.strip().lower().startswith("t") else False,
             "role holds CREATEDB" if out.strip().lower().startswith("t")
             else "role lacks CREATEDB (managed posture or plain user)")
    elif "does not exist" in low and "role" in low:
        _add("db_user", False, f"role '{user}' does not exist")
        _add("db_password", None, "skipped — role missing")
        _add("can_create", None, "skipped — role missing")
    elif "password authentication failed" in low or "no password supplied" in low:
        # Note: most servers answer this way even for nonexistent roles (no
        # existence leak), so role existence is UNKNOWN here, not confirmed.
        _add("db_user", None, f"role '{user}' unverified — authentication failed")
        _add("db_password", False, "Postgres rejected the stored password")
        _add("can_create", None, "skipped — not authenticated")
    else:
        hint = (out.splitlines() or ["unknown error"])[0][:160]
        _add("db_user", False, f"probe failed: {hint}")
        _add("db_password", None, "skipped — probe failed")
        _add("can_create", None, "skipped — probe failed")

    primary = (instance.primary_db or "").strip()
    if not primary:
        _add("primary_db", False, "instance records no primary database")
    else:
        st = get_db_state(primary, user, password or None)
        if st.error and not st.exists:
            _add("primary_db", None, f"could not verify: {st.error}")
        elif not st.exists:
            _add("primary_db", False, f"'{primary}' does not exist")
        elif not st.initialized:
            _add("primary_db", False, f"'{primary}' exists but is not initialized")
        else:
            _add("primary_db", True,
                 f"'{primary}' exists, initialized"
                 + (f" ({st.odoo_version})" if st.odoo_version else ""))
    return {"checks": checks}


def human_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "—"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
