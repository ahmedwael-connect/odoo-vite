"""SQLite-backed instance registry (§1.2 of the Phase 1 charter).

DB file: ~/.local/share/odoo-vite/odoo_vite.db (created on first run).
Override for tests via ODOO_VITE_DB env var or the db_path argument.

stdlib sqlite3 only. No GTK imports.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from odoo_vite.core.instance import Instance
from odoo_vite.core.result import Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'managed',
    path TEXT NOT NULL DEFAULT '',
    venv_path TEXT NOT NULL DEFAULT '',
    community_path TEXT NOT NULL DEFAULT '',
    enterprise_path TEXT,
    custom_addons_path TEXT NOT NULL DEFAULT '',
    conf_path TEXT NOT NULL DEFAULT '',
    log_path TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 8069,
    db_user TEXT NOT NULL DEFAULT 'odoo',
    db_password TEXT NOT NULL DEFAULT '',
    password_storage TEXT NOT NULL DEFAULT 'plaintext',
    primary_db TEXT NOT NULL DEFAULT '',
    tracked_dbs TEXT NOT NULL DEFAULT '[]',
    auto_update_modules TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    pid INTEGER,
    last_error TEXT,
    db_created INTEGER NOT NULL DEFAULT 0,
    provisioning_mode TEXT NOT NULL DEFAULT 'developer',
    description TEXT NOT NULL DEFAULT '',
    workers INTEGER NOT NULL DEFAULT 0,
    log_level TEXT NOT NULL DEFAULT 'info',
    python_binary TEXT NOT NULL DEFAULT '',
    addons_state TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""

# NOTE: the settings table (Phase 1.5 core/settings.py) is created alongside.
SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_COLUMNS = [
    "id", "name", "version", "mode", "path", "venv_path", "community_path",
    "enterprise_path", "custom_addons_path", "conf_path", "log_path", "port",
    "db_user", "db_password", "password_storage", "primary_db", "tracked_dbs",
    "auto_update_modules", "status", "pid", "last_error", "db_created",
    "provisioning_mode", "description", "workers", "log_level",
    "python_binary", "addons_state", "created_at",
]

# Sprint 2+3 additive migrations: (column, DDL fragment). Applied by
# ensure_schema()/every connect via PRAGMA table_info (no schema_version table —
# few columns only; a version table would be cleaner past ~5 migrations).
_MIGRATIONS = [
    ("password_storage", "ALTER TABLE instances ADD COLUMN password_storage TEXT NOT NULL DEFAULT 'plaintext'"),
    ("last_error", "ALTER TABLE instances ADD COLUMN last_error TEXT"),
    ("db_created", "ALTER TABLE instances ADD COLUMN db_created INTEGER NOT NULL DEFAULT 0"),
    ("provisioning_mode", "ALTER TABLE instances ADD COLUMN provisioning_mode TEXT NOT NULL DEFAULT 'developer'"),
    ("description", "ALTER TABLE instances ADD COLUMN description TEXT NOT NULL DEFAULT ''"),
    ("workers", "ALTER TABLE instances ADD COLUMN workers INTEGER NOT NULL DEFAULT 0"),
    ("log_level", "ALTER TABLE instances ADD COLUMN log_level TEXT NOT NULL DEFAULT 'info'"),
    ("python_binary", "ALTER TABLE instances ADD COLUMN python_binary TEXT NOT NULL DEFAULT ''"),
    ("addons_state", "ALTER TABLE instances ADD COLUMN addons_state TEXT NOT NULL DEFAULT ''"),
]

KEYRING_SERVICE = "odoo-vite"


def get_db_path() -> Path:
    override = os.environ.get("ODOO_VITE_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "odoo-vite" / "odoo_vite.db"


def _ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """Apply additive migrations. Returns names of columns added (or []).

    3.1.0 B7: the check-then-act used to run unlocked, so two pywebview
    threads connecting during an upgrade both fired the same ALTER and
    the loser died with `duplicate column name`. Pending migrations are
    now re-checked under BEGIN IMMEDIATE (SQLite's write lock); the
    common no-migration path stays lock-free.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(instances)")}
    if all(column in existing for column, _ddl in _MIGRATIONS):
        return []
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(instances)")
        }
        added = []
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                conn.execute(ddl)
                added.append(column)
        conn.commit()
        return added
    except BaseException:
        conn.rollback()
        raise


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executescript(SETTINGS_SCHEMA)
    _ensure_columns(conn)
    return conn


def ensure_schema(db_path: Path | str | None = None) -> Result:
    """Create/migrate the registry schema (idempotent).

    Fresh DBs get the full Sprint 2 schema; Sprint 1 DBs gain
    password_storage/last_error via additive ALTER TABLE.
    """
    try:
        with _connect(db_path) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(instances)")]
        resolved = str(Path(db_path).expanduser()) if db_path else str(get_db_path())
        return Result.success(
            data={"db_path": resolved, "columns": cols},
            message="Registry schema ready",
        )
    except OSError as exc:
        return Result.failure(f"Cannot create registry database: {exc}")


def init_db(db_path: Path | str | None = None) -> Result:
    """Create the registry file + schema (idempotent)."""
    res = ensure_schema(db_path)
    if not res.ok:
        return Result.failure(f"Cannot create registry database: {res.message}")
    return Result.success(
        data={"db_path": res.data["db_path"]}, message="Registry ready"
    )


def create_instance(instance: Instance, db_path: Path | str | None = None) -> Result:
    """Insert a new instance row. Fails cleanly on duplicate name."""
    try:
        # RC decision (matrix #11): names are unique case-INSENSITIVELY —
        # "Test" vs "test" would slugify to the same folder and fold to the
        # same Postgres identifier, so allowing both invites real breakage.
        if get_instance_by_name(instance.name, db_path) is not None:
            return Result.failure(
                f"Instance '{instance.name}' already exists "
                "(names are case-insensitive)")
        row = instance.to_row()
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        with _connect(db_path) as conn:
            conn.execute(f"INSERT INTO instances ({cols}) VALUES ({placeholders})", row)
            conn.commit()
        return Result.success(data={"id": instance.id}, message=f"Instance '{instance.name}' registered")
    except sqlite3.IntegrityError as exc:
        return Result.failure(f"Instance '{instance.name}' already exists ({exc})")
    except OSError as exc:
        return Result.failure(f"Cannot write registry database: {exc}")


def list_instances(db_path: Path | str | None = None) -> list[Instance]:
    """Return all registered instances (empty list on fresh/missing DB)."""
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("SELECT * FROM instances ORDER BY created_at ASC")
            return [Instance.from_row(dict(r)) for r in cur.fetchall()]
    except OSError:
        return []


def get_instance(instance_id: str, db_path: Path | str | None = None) -> Instance | None:
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("SELECT * FROM instances WHERE id = ?", (instance_id,))
            row = cur.fetchone()
            return Instance.from_row(dict(row)) if row else None
    except OSError:
        return None


def get_instance_by_name(name: str, db_path: Path | str | None = None) -> Instance | None:
    """Case-insensitive lookup (RC #11: Test == test for uniqueness)."""
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("SELECT * FROM instances WHERE name = ? COLLATE NOCASE", (name,))
            row = cur.fetchone()
            return Instance.from_row(dict(row)) if row else None
    except OSError:
        return None


def update_instance(
    instance_id: str, db_path: Path | str | None = None, **fields: Any
) -> Result:
    """Update whitelisted columns of an instance row."""
    allowed = {c for c in _COLUMNS if c != "id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return Result.failure("Nothing to update (no valid fields given)")
    import json as _json

    for key in ("tracked_dbs", "auto_update_modules", "addons_state"):
        if key in updates and isinstance(updates[key], (list, tuple)):
            updates[key] = _json.dumps(list(updates[key]))
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                f"UPDATE instances SET {set_clause} WHERE id = :_id",
                {**updates, "_id": instance_id},
            )
            conn.commit()
            if cur.rowcount == 0:
                return Result.failure(f"No instance with id '{instance_id}'")
        return Result.success(data={"id": instance_id}, message="Instance updated")
    except sqlite3.IntegrityError as exc:
        return Result.failure(f"Update violates uniqueness ({exc})")
    except OSError as exc:
        return Result.failure(f"Cannot write registry database: {exc}")


def delete_instance(instance_id: str, db_path: Path | str | None = None) -> Result:
    """Delete a registry row (files on disk are Sprint 4 scope — untouched here)."""
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
            conn.commit()
            if cur.rowcount == 0:
                return Result.failure(f"No instance with id '{instance_id}'")
        return Result.success(data={"id": instance_id}, message="Instance removed from registry")
    except OSError as exc:
        return Result.failure(f"Cannot write registry database: {exc}")


# ---------------------------------------------------------------------------
# DB password storage: OS keyring primary (Phase 1.5 H.2: fail loudly —
# no silent plaintext fallback unless the caller passes an explicit,
# user-confirmed allow_plaintext opt-out).

def keyring_available() -> bool:
    """Probe whether a working keyring/Secret Service backend exists."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, "__probe__", "probe")
        try:
            ok = keyring.get_password(KEYRING_SERVICE, "__probe__") == "probe"
        finally:
            try:
                keyring.delete_password(KEYRING_SERVICE, "__probe__")
            except Exception:
                pass
        return bool(ok)
    except Exception:
        return False


def store_db_password(instance_id: str, password: str,
                      allow_plaintext: bool = False) -> tuple[str, str]:
    """Persist a DB password. Returns (password_storage, value_for_db_column).

    ("keyring", "") on success. ("plaintext", password) only with an explicit
    user opt-out (allow_plaintext=True). Otherwise ("unavailable", password) —
    the caller must stop and tell the user, never silently degrade.
    Never raises.
    """
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, f"{instance_id}:db", password)
        if keyring.get_password(KEYRING_SERVICE, f"{instance_id}:db") != password:
            raise ValueError("keyring write could not be read back")
        return ("keyring", "")
    except Exception:
        if allow_plaintext:
            return ("plaintext", password)
        return ("unavailable", password)


def get_db_password(instance: Instance) -> str:
    """Resolve the real DB password for an instance (either backend)."""
    if instance.password_storage == "keyring":
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, f"{instance.id}:db") or ""
        except Exception:
            return ""
    return instance.db_password


def delete_db_password(instance_id: str) -> None:
    """Best-effort keyring cleanup (e.g. on Discard). Never raises."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, f"{instance_id}:db")
    except Exception:
        pass


def migrate_password_to_keyring(instance_id: str, db_path=None) -> Result:
    """One-click sweep: move a plaintext password into the keyring (H.2)."""
    from odoo_vite.core import audit as audit_log

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    if inst.password_storage == "keyring":
        return Result.success(message="Password is already in the keyring")
    if not inst.db_password:
        return Result.failure("No plaintext password stored to migrate")
    storage, column = store_db_password(instance_id, inst.db_password,
                                        allow_plaintext=False)
    if storage != "keyring":
        return Result.failure(
            "Keyring still unavailable — enable a Secret Service "
            "(e.g. 'sudo apt install gnome-keyring' + relogin) and retry")
    res = update_instance(instance_id, db_path, password_storage="keyring",
                          db_password="")
    if not res.ok:
        return Result.failure(f"Keyring updated but registry write failed: {res.message}")
    try:
        audit_log.log_event(instance_id, inst.name, "secret_secured",
                            "plaintext password moved to OS keyring")
    except Exception:
        pass
    return Result.success(message="Password moved to the OS keyring")
