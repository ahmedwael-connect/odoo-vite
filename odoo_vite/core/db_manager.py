"""Postgres role helpers (Sprint 2 start of this module — Ticket 2.6).

ensure_role(db_user, db_password) creates the Odoo DB role if missing and
grants CREATEDB (Odoo creates/drops its working databases in several flows).

Approach (noted for the Sprint 2 report): probes go over TCP
(-h localhost) with PGPASSWORD so an existing role needs no escalation;
creation/privilege fixes run a single idempotent DO+ALTER script via
`pkexec --user postgres psql` — one GUI auth prompt, no terminal sudo
(pkexec was approved by PM for Sprint 1 and carried over).
Sprint 4 will extend this module (list/switch/track databases).

No GTK imports.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from odoo_vite.core.result import Result

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def is_valid_identifier(name: str) -> bool:
    """Postgres identifier safety (role names and database names)."""
    return bool(IDENTIFIER_RE.match(name or ""))


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qliteral(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(cmd: list[str], env_extra: dict[str, str] | None = None,
         timeout: int = 60) -> tuple[int, str]:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    except OSError as exc:
        return -1, f"cannot start {cmd[0]}: {exc}"


def server_reachable() -> bool:
    """True if a Postgres server answers (pg_isready preferred, psql probe fallback)."""
    if shutil.which("pg_isready"):
        rc, _ = _run(["pg_isready", "-h", "localhost", "-p", "5432"])
        return rc == 0
    if not shutil.which("psql"):
        return False
    rc, _ = _run(["psql", "-h", "localhost", "-U", "postgres",
                  "-d", "postgres", "-c", "SELECT 1"])
    return rc == 0


def role_exists(db_user: str, db_password: str | None = None) -> bool:
    """Best-effort check whether the role is usable with no escalation.

    Tries passwordless first (peer/trust setups), then with db_password
    when given (md5/scram host auth).
    """
    if not shutil.which("psql"):
        return False
    attempts: list[dict[str, str] | None] = [None]
    if db_password:
        attempts.append({"PGPASSWORD": db_password})
    for env_extra in attempts:
        rc, out = _run(["psql", "-h", "localhost", "-U", db_user,
                        "-d", "postgres", "-tAc", "SELECT 1"],
                       env_extra=env_extra)
        if rc == 0 and out.strip().startswith("1"):
            return True
    return False


def role_has_createdb(db_user: str, db_password: str) -> bool | None:
    """None when the role cannot be probed (missing/wrong password)."""
    if not shutil.which("psql"):
        return None
    rc, out = _run(
        ["psql", "-h", "localhost", "-U", db_user, "-d", "postgres",
         "-tAc", "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"],
        env_extra={"PGPASSWORD": db_password},
    )
    if rc != 0:
        return None
    return out.strip().lower().startswith("t")


def ensure_role(db_user: str, db_password: str, dry_run: bool = False,
                createdb: bool | None = None) -> Result:
    """Create the role if missing; ensure LOGIN + password (Ticket 2.6,
    Phase 1.5 H.1).

    createdb=None resolves from the global provisioning_mode: "developer"
    (default) grants CREATEDB as before; "managed" creates NOCREATEDB roles
    and never grants CREATEDB — not even to pre-existing roles (no silent
    privilege changes in either direction).
    """
    if not is_valid_identifier(db_user):
        return Result.failure(
            f"Invalid Postgres role name '{db_user}' "
            "(must match ^[a-z_][a-z0-9_]*$)"
        )
    if shutil.which("psql") is None:
        return Result.failure(
            "psql not found — install the PostgreSQL client "
            "('pkexec apt-get install -y postgresql-client') and retry"
        )
    if createdb is None:
        try:
            from odoo_vite.core.settings import get_provisioning_mode

            createdb = get_provisioning_mode() == "developer"
        except Exception:
            createdb = True

    if createdb:
        script = (
            f"DO $$ BEGIN IF NOT EXISTS "
            f"(SELECT FROM pg_catalog.pg_roles WHERE rolname = {_qliteral(db_user)}) THEN "
            f"CREATE ROLE {_qident(db_user)} WITH LOGIN CREATEDB; END IF; END $$; "
            f"ALTER ROLE {_qident(db_user)} WITH LOGIN CREATEDB PASSWORD {_qliteral(db_password)};"
        )
    else:
        script = (
            f"DO $$ BEGIN IF NOT EXISTS "
            f"(SELECT FROM pg_catalog.pg_roles WHERE rolname = {_qliteral(db_user)}) THEN "
            f"CREATE ROLE {_qident(db_user)} WITH LOGIN NOCREATEDB; END IF; END $$; "
            f"ALTER ROLE {_qident(db_user)} WITH LOGIN PASSWORD {_qliteral(db_password)};"
        )
    privileged = ["pkexec", "--user", "postgres", "psql",
                  "-v", "ON_ERROR_STOP=1", "-c", script]

    if dry_run:
        return Result.success(
            data={"command": privileged, "script": script,
                  "createdb": bool(createdb)},
            message="Dry run: " + " ".join(privileged[:4]) + " ...",
        )

    if createdb:
        # Fast path: role already usable with full privileges — no prompt needed.
        if role_exists(db_user, db_password) and role_has_createdb(db_user, db_password) is True:
            return Result.success(
                data={"db_user": db_user, "created": False},
                message=f"Postgres role '{db_user}' already ready",
            )
    else:
        # Managed mode: usable login is enough; never assert or grant CREATEDB.
        if role_exists(db_user, db_password):
            return Result.success(
                data={"db_user": db_user, "created": False},
                message=f"Postgres role '{db_user}' already ready (least-privilege)",
            )

    if not server_reachable():
        return Result.failure(
            "PostgreSQL server is not reachable on localhost:5432. "
            "Start it (e.g. 'sudo pg_ctlcluster 16 main start' or "
            "'sudo systemctl start postgresql') and retry."
        )
    if shutil.which("pkexec") is None:
        return Result.failure(
            f"Role '{db_user}' needs creating/fixing but pkexec is missing. "
            f"Run once as the postgres superuser: "
            f"sudo -u postgres psql -c \"CREATE ROLE {_qident(db_user)} "
            f"WITH LOGIN CREATEDB PASSWORD '***';\""
        )

    rc, out = _run(privileged, timeout=120)
    if rc != 0:
        hint = out.splitlines()[-1] if out else f"exit {rc}"
        return Result.failure(
            f"Could not ensure Postgres role '{db_user}': {hint} "
            "(dismissing the auth dialog or a locked-down Postgres also lands here — "
            "fallback: sudo -u postgres createuser -s "
            + db_user + ")"
        )
    priv_note = "LOGIN + CREATEDB" if createdb else "LOGIN, least-privilege (no CREATEDB)"
    return Result.success(
        data={"db_user": db_user, "created": True, "createdb": bool(createdb)},
        message=f"Postgres role '{db_user}' ready ({priv_note})",
    )


def create_database(
    db_name: str, db_user: str = "odoo", db_password: str | None = None,
    dry_run: bool = False,
) -> Result:
    """CREATE DATABASE as an explicit, separately-privileged op (H.1).

    Owner fast path first (works when the role holds CREATEDB, i.e. developer
    mode); on permission-denied, one privileged run via pkexec-as-postgres
    (the GUI prompt lands exactly at the moment of need, not as a standing
    grant). Guards mirror drop_database.
    """
    if not is_valid_identifier(db_name):
        return Result.failure(f"Refusing to create invalid database name '{db_name}'")
    if db_name in ("postgres", "template0", "template1"):
        return Result.failure(f"Refusing to touch system database '{db_name}'")
    if shutil.which("psql") is None:
        return Result.failure("psql not found — cannot create database")

    owner_cmd = ["psql", "-h", "localhost", "-U", db_user, "-d", "postgres",
                 "-v", "ON_ERROR_STOP=1", "-c",
                 f"CREATE DATABASE {_qident(db_name)} OWNER {_qident(db_user)};"]
    # BUG-2 fix (RC): on PG15+ the public schema is locked down, so a fresh
    # DB's owner is its only writer. The privileged path must therefore also
    # grant schema access, or a managed instance's -i base dies with
    # "permission denied for schema public". (Owner-created DBs don't need
    # this: owners are implicit pg_database_owner members.)
    grant_sql = (f"GRANT ALL ON SCHEMA public TO {_qident(db_user)};")
    privileged_cmd = ["pkexec", "--user", "postgres", "psql",
                      "-v", "ON_ERROR_STOP=1", "-c",
                      f"CREATE DATABASE {_qident(db_name)} OWNER {_qident(db_user)};",
                      "-c", grant_sql]
    if dry_run:
        return Result.success(
            data={"owner_command": owner_cmd, "privileged_command": privileged_cmd},
            message="Dry run: owner path, then pkexec fallback on permission-denied",
        )
    rc, out = _run(owner_cmd,
                   env_extra={"PGPASSWORD": db_password} if db_password else None,
                   timeout=120)
    if rc == 0:
        return Result.success(data={"db_name": db_name, "privileged": False},
                              message=f"Database '{db_name}' created")
    lowered = (out or "").lower()
    if "already exists" in lowered:
        return Result.failure(
            f"Database '{db_name}' already exists",
            data={"collision": True, "db_name": db_name})
    if "permission denied" not in lowered:
        hint = out.splitlines()[-1] if out else f"exit {rc}"
        return Result.failure(f"Could not create database '{db_name}': {hint}")
    if shutil.which("pkexec") is None:
        return Result.failure(
            f"Role '{db_user}' may not create databases and pkexec is missing. "
            f"Run once as superuser: sudo -u postgres createdb -O {db_user} {db_name}")
    rc2, out2 = _run(privileged_cmd, timeout=120)
    if rc2 != 0:
        hint = out2.splitlines()[-1] if out2 else f"exit {rc2}"
        return Result.failure(
            f"Could not create database '{db_name}' (privileged path): {hint}")
    return Result.success(data={"db_name": db_name, "privileged": True},
                          message=f"Database '{db_name}' created (privileged)")


def list_databases(*args, **kwargs) -> Result:  # Sprint 4 scope
    return Result.failure("db_manager.list_databases is Sprint 4 scope")


def drop_database(
    db_name: str, db_user: str = "odoo", db_password: str | None = None
) -> Result:
    """DROP DATABASE via psql as the owner role (Sprint 4).

    Guards: valid identifier, never postgres/template0/template1.
    The caller (removal) stops the instance first, so no live connections
    should remain; a failure names the likely cause.
    """
    if not is_valid_identifier(db_name):
        return Result.failure(f"Refusing to drop invalid database name '{db_name}'")
    if db_name in ("postgres", "template0", "template1"):
        return Result.failure(f"Refusing to drop system database '{db_name}'")
    if shutil.which("psql") is None:
        return Result.failure("psql not found — cannot drop database")
    rc, out = _run(
        ["psql", "-h", "localhost", "-U", db_user, "-d", "postgres",
         "-v", "ON_ERROR_STOP=1", "-c", f"DROP DATABASE {_qident(db_name)};"],
        env_extra={"PGPASSWORD": db_password} if db_password else None,
        timeout=120,
    )
    if rc != 0:
        hint = out.splitlines()[-1] if out else f"exit {rc}"
        return Result.failure(
            f"Could not drop database '{db_name}': {hint} "
            "(often: active connections remain, or the role lacks rights)")
    return Result.success(
        data={"db_name": db_name}, message=f"Database '{db_name}' dropped")


def database_exists(
    db_name: str, db_user: str = "odoo", db_password: str | None = None
) -> bool:
    """Thin wrapper over db_state.get_db_state (Sprint 5 Part A)."""
    try:
        from odoo_vite.core.db_state import get_db_state

        return bool(get_db_state(db_name, db_user, db_password).exists)
    except Exception:
        return False


def database_initialized(
    db_name: str, db_user: str = "odoo", db_password: str | None = None
) -> bool:
    """Thin wrapper over db_state.get_db_state (Sprint 5 Part A)."""
    try:
        from odoo_vite.core.db_state import get_db_state

        return bool(get_db_state(db_name, db_user, db_password).initialized)
    except Exception:
        return False


def list_databases_for_user(
    db_user: str, db_password: str | None = None
) -> Result:
    """List non-template databases visible to db_user (Sprint 4, Ticket 4.6).

    Implemented with subprocess psql -tAc — same transport as the rest of
    this module (no psycopg2 dependency, consistent auth handling).
    """
    if not is_valid_identifier(db_user):
        return Result.failure(f"Invalid Postgres user name '{db_user}'")
    if shutil.which("psql") is None:
        return Result.failure("psql not found — cannot list databases")
    rc, out = _run(
        ["psql", "-h", "localhost", "-U", db_user, "-d", "postgres",
         "-tAc", "SELECT datname FROM pg_database "
                  "WHERE datistemplate = false ORDER BY datname;"],
        env_extra={"PGPASSWORD": db_password} if db_password else None,
    )
    if rc != 0:
        hint = out.splitlines()[-1] if out else f"exit {rc}"
        return Result.failure(f"Could not list databases for '{db_user}': {hint}")
    databases = [line.strip() for line in out.splitlines() if line.strip()]
    return Result.success(data={"databases": databases},
                          message=f"{len(databases)} database(s) found")


def track_database(instance_id: str, db_name: str, db_path=None) -> Result:
    """Append db_name to tracked_dbs (idempotent, no duplicates)."""
    from odoo_vite.core.registry import get_instance, update_instance

    name = (db_name or "").strip()
    if not is_valid_identifier(name):
        return Result.failure(f"Invalid database name '{db_name}'")
    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    tracked = list(inst.tracked_dbs or [])
    if name in tracked:
        return Result.success(data={"tracked_dbs": tracked},
                              message=f"'{name}' is already tracked")
    tracked.append(name)
    res = update_instance(instance_id, db_path, tracked_dbs=tracked)
    if not res.ok:
        return Result.failure(f"Cannot track '{name}': {res.message}")
    return Result.success(data={"tracked_dbs": tracked},
                          message=f"'{name}' is now tracked")


def untrack_database(instance_id: str, db_name: str, db_path=None) -> Result:
    """Remove db_name from tracked_dbs (missing entry is a safe no-op).

    Untracking the primary is allowed; data flags was_primary so the UI
    can warn that it disappears from the switch list (nothing stops running).
    """
    from odoo_vite.core.registry import get_instance, update_instance

    name = (db_name or "").strip()
    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    tracked = list(inst.tracked_dbs or [])
    was_primary = (name == (inst.primary_db or ""))
    if name not in tracked:
        return Result.success(data={"tracked_dbs": tracked,
                                    "was_primary": was_primary},
                              message=f"'{name}' was not tracked — nothing to do")
    tracked.remove(name)
    res = update_instance(instance_id, db_path, tracked_dbs=tracked)
    if not res.ok:
        return Result.failure(f"Cannot untrack '{name}': {res.message}")
    msg = f"'{name}' untracked"
    if was_primary:
        msg += " (note: it is still the primary database — running instance unaffected)"
    return Result.success(data={"tracked_dbs": tracked, "was_primary": was_primary},
                          message=msg)
