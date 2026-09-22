"""Instance process lifecycle (Sprint 3, Tickets 3.2–3.5).

- start_instance: launch odoo-bin (first start builds -i base behind an
  explicit confirm_cb + DB-collision check, never silently).
- stop_instance: SIGTERM → poll → SIGKILL fallback, with PID-reuse safety
  (psutil liveness + cmdline match before signalling anything).
- restart_instance: stop + start composition.
- get_statuses: batch poll for the UI (~2s), self-healing stale "running"
  rows and reporting CPU%/RSS for live processes.

Reuse contract for the UI's collision dialog (no extra params needed):
  "Reuse existing" → registry.update_instance(id, db_created=True), then
  start (plain `-d`, no `-i base`). "Pick new name" → update primary_db
  (db_created stays False), then start (confirm + create the new DB).
  "Abort" → do nothing.

No GTK imports.
"""

from __future__ import annotations

import fcntl
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import psutil

from odoo_vite.core import audit as audit_log
from odoo_vite.core.result import Result

ConfirmCb = Callable[[dict], bool]


def _locks_dir() -> Path:
    """Our own lock dir (never inside instance/adopted folders)."""
    import os

    override = os.environ.get("ODOO_VITE_LOCKS")
    base = Path(override).expanduser() if override else (
        Path.home() / ".local" / "share" / "odoo-vite" / "locks")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _acquire_start_lock(instance_id: str):
    """Non-blocking exclusive start lock (cross-process). File obj or None.

    Closes the Sprint-3 double-start race: the registry write happens ~1s
    after Popen (liveness sleep), so two rapid Starts could both pass the
    already-running check. flock is kernel-tracked: no stale locks on crash.
    """
    try:
        fh = open(_locks_dir() / f"{instance_id}.start.lock", "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        return None


def _release_start_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    except OSError:
        pass


def _alive_pid(instance) -> int | None:  # type: ignore[no-untyped-def]
    """Return the pid if it is alive AND looks like this instance's odoo-bin."""
    pid = instance.pid
    if not pid:
        return None
    try:
        if not psutil.pid_exists(int(pid)):
            return None
        proc = psutil.Process(int(pid))
        if _cmdline_matches(proc, instance):
            return int(pid)
        return None
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def _cmdline_matches(proc: psutil.Process, instance) -> bool:  # type: ignore[no-untyped-def]
    """Guard against PID reuse: only match our own odoo-bin command line."""
    try:
        joined = " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if "odoo-bin" not in joined:
        return False
    community = (instance.community_path or "").strip()
    conf = (instance.conf_path or "").strip()
    if community or conf:
        return (community and community in joined) or (conf and conf in joined)
    return True


def _password_for(instance) -> str:  # type: ignore[no-untyped-def]
    from odoo_vite.core.registry import get_db_password

    try:
        return get_db_password(instance) or ""
    except Exception:
        return instance.db_password or ""


def _build_command(instance, database: str, first_start: bool) -> list[str]:  # type: ignore[no-untyped-def]
    venv_python = str(Path(instance.venv_path) / "bin" / "python")
    odoo_bin = str(Path(instance.community_path) / "odoo-bin")
    cmd = [venv_python, odoo_bin, "-c", instance.conf_path, "-d", database]
    if first_start:
        cmd.extend(["-i", "base"])
    mods = [m.strip() for m in (instance.auto_update_modules or []) if m.strip()]
    if mods:
        cmd.extend(["-u", ",".join(mods)])
    return cmd


def start_instance(
    instance_id: str,
    database: str | None = None,
    confirm_cb: ConfirmCb | None = None,
    db_path=None,
) -> Result:
    """Start (or first-create + start) an instance. See module docstring."""
    from odoo_vite.core import db_manager
    from odoo_vite.core.registry import get_instance, update_instance

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")

    live = _alive_pid(inst)
    if live is not None:
        return Result.failure(
            f"Instance '{inst.name}' is already running (pid {live})",
            data={"pid": live, "port": inst.port},
        )

    target_db = (database or inst.primary_db or "").strip()
    if not target_db:
        return Result.failure(
            f"Instance '{inst.name}' has no database configured — "
            "set a primary database first"
        )

    # BUG-3 (RC): ground truth is initialization state, not just the
    # db_created flag. A launch can succeed (process stays up) while -i base
    # later fails, leaving db_created=True on an empty DB — subsequent plain
    # starts would then serve HTTP 500s forever with no recovery path.
    pw = _password_for(inst) or None
    exists = db_manager.database_exists(target_db, inst.db_user, pw)
    initialized = db_manager.database_initialized(target_db, inst.db_user, pw)
    first_start = not bool(inst.db_created)
    needs_init = (not inst.db_created) or (not initialized)

    # Preconditions with actionable messages (before touching the registry).
    # H-B2: adopted rows may record no venv at all — that needs its own
    # message (pointing at the detail-page editor), never a misleading
    # relative "bin/python" path plus "re-run provisioning".
    if not (inst.venv_path or "").strip():
        return Result.failure(
            f"No Python environment recorded for adopted instance '{inst.name}' — "
            "open its detail page and set the venv Python path "
            "(a virtualenv folder containing bin/python), then Start again"
            if (inst.mode or "managed") == "adopted" else
            f"Instance '{inst.name}' has no venv path configured — re-run provisioning"
        )
    venv_python = Path(inst.venv_path) / "bin" / "python"
    odoo_bin = Path(inst.community_path) / "odoo-bin"
    if not venv_python.is_file():
        hint = ("re-run provisioning"
                if (inst.mode or "managed") == "managed"
                else "check the venv path recorded on the detail page")
        return Result.failure(
            f"Venv python missing at {venv_python} — {hint}"
        )
    if not odoo_bin.is_file():
        return Result.failure(
            f"odoo-bin missing at {odoo_bin} — re-run provisioning"
        )
    if inst.conf_path and not Path(inst.conf_path).is_file():
        return Result.failure(
            f"odoo.conf missing at {inst.conf_path} — re-run provisioning"
        )

    cmd = _build_command(inst, target_db, needs_init)

    reinit = False  # exists-but-empty DB: -i base below completes its init
    if needs_init:
        preview = {
            "instance_name": inst.name,
            "db_name": target_db,
            "conf_path": inst.conf_path,
            "command": list(cmd),
            "command_str": " ".join(cmd),
            "reinit": bool(exists and not initialized),
        }
        if confirm_cb is None:
            what = ("complete the initialization of" if preview["reinit"]
                    else "create database")
            return Result.failure(
                f"Start of '{inst.name}' would {what} "
                f"'{target_db}' — explicit confirmation required "
                "(no confirm callback supplied; refusing to auto-create)",
                data={"needs_confirm": True, "preview": preview},
            )
        try:
            confirmed = bool(confirm_cb(preview))
        except Exception as exc:
            return Result.failure(f"Confirmation step errored: {exc}")
        if not confirmed:
            return Result.failure("Database creation cancelled by user")
        if exists and initialized and not inst.db_created:
            return Result.failure(
                f"Database '{target_db}' already exists — refusing to "
                "silently reuse or overwrite",
                data={"collision": True, "db_name": target_db, "preview": preview},
            )
        # Exists-but-empty (interrupted setup): -i base below completes it.
        reinit = bool(exists and not initialized)

        # H.1 managed mode: the role cannot create databases itself, so the
        # empty DB is created here as an explicit privileged op (the user
        # already confirmed creation above); -i base below initializes it.
        if (inst.provisioning_mode or "developer") == "managed" and not exists:
            create_res = db_manager.create_database(target_db, inst.db_user, pw)
            if not create_res.ok:
                return Result.failure(
                    f"Managed mode: {create_res.message}")

    # RC: serialize launches — the registry write lands ~1s after Popen
    # (liveness sleep), so lock here, after confirm, before touching the OS.
    lock_fh = _acquire_start_lock(instance_id)
    if lock_fh is None:
        return Result.failure(
            f"Start already in progress for '{inst.name}' — refusing a "
            "second concurrent launch")
    try:
        return _launch_locked(inst, instance_id, target_db, cmd, log_path_hint(inst),
                              needs_init, reinit, db_path)
    finally:
        _release_start_lock(lock_fh)


def log_path_hint(inst) -> Path:  # type: ignore[no-untyped-def]
    return Path(inst.log_path) if inst.log_path else Path(inst.path) / "logs" / "odoo.log"


def _launch_locked(inst, instance_id: str, target_db: str, cmd: list,  # type: ignore[no-untyped-def]
                   log_path: Path, needs_init: bool, reinit: bool, db_path) -> Result:
    from odoo_vite.core.registry import update_instance

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(log_path, "a", encoding="utf-8")
    except OSError as exc:
        return Result.failure(f"Cannot open log file: {exc}")

    try:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        try:
            logf.close()
        except OSError:
            pass
        return Result.failure(f"Cannot launch odoo-bin: {exc}")
    finally:
        # The child holds its own fd; the parent end can close right away.
        try:
            logf.close()
        except OSError:
            pass

    time.sleep(1)
    if proc.poll() is not None:
        tail = _tail_log(log_path, 10)
        msg = (
            f"Odoo for '{inst.name}' exited immediately "
            f"(code {proc.returncode})"
            + (f" — log tail:\n{tail}" if tail else "")
        )
        update_instance(instance_id, db_path, status="error", last_error=msg)
        audit_log.log_event(inst.id, inst.name, "start", f"immediate exit: {msg[:300]}")
        return Result.failure(msg)

    update_instance(
        instance_id, db_path,
        pid=proc.pid, status="running",
        primary_db=target_db, db_created=True, last_error=None,
    )
    audit_log.log_event(inst.id, inst.name, "start",
                        f"pid={proc.pid} db={target_db} needs_init={needs_init}"
                        + (" reinit-uninitialized" if reinit else ""))
    if needs_init:
        audit_log.log_event(inst.id, inst.name, "db_create",
                            f"database '{target_db}' created via -i base"
                            + (" (completed interrupted init)" if reinit else ""))
    return Result.success(
        data={"pid": proc.pid, "port": inst.port, "database": target_db},
        message=f"Instance '{inst.name}' started (pid {proc.pid})",
    )


def _tail_log(log_path: Path, lines: int) -> str:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).strip()
    except OSError:
        return ""


def stop_instance(instance_id: str, timeout: int = 15, db_path=None) -> Result:
    """Graceful SIGTERM → poll → SIGKILL fallback. Never kills strangers."""
    from odoo_vite.core.registry import get_instance, update_instance

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")

    pid = inst.pid
    proc = None
    if pid:
        try:
            candidate = psutil.Process(int(pid))
            alive = candidate.is_running() and psutil.pid_exists(int(pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            alive = False
            candidate = None
        if alive and candidate is not None and _cmdline_matches(candidate, inst):
            proc = candidate
        elif alive:
            # PID reuse by an unrelated process — correct the row, touch nothing.
            update_instance(instance_id, db_path, status="stopped", pid=None)
            return Result.success(
                message=f"Instance '{inst.name}' was already stopped "
                        f"(stale pid {pid} belongs to another process — corrected)"
            )

    if proc is None:
        update_instance(instance_id, db_path, status="stopped", pid=None)
        return Result.success(message=f"Instance '{inst.name}' is already stopped")

    try:
        proc.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        update_instance(instance_id, db_path, status="stopped", pid=None)
        return Result.success(message=f"Instance '{inst.name}' already exiting ({exc})")

    deadline = time.monotonic() + max(int(timeout), 1)
    graceful = False
    while time.monotonic() < deadline:
        try:
            if not proc.is_running() or proc.wait(timeout=0.5) is not None:
                graceful = True
                break
        except psutil.TimeoutExpired:
            continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            graceful = True
            break

    if not graceful:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            return Result.failure(
                f"Instance '{inst.name}' (pid {pid}) would not die even "
                "after SIGKILL — investigate manually"
            )
        update_instance(instance_id, db_path, status="stopped", pid=None)
        audit_log.log_event(inst.id, inst.name, "forced_kill",
                            f"pid {pid} ignored SIGTERM for {timeout}s; SIGKILL used")
        audit_log.log_event(inst.id, inst.name, "stop", "forced (SIGKILL)")
        return Result.success(
            message=f"Instance '{inst.name}' stopped (graceful shutdown "
                    f"failed after {timeout}s — SIGKILL was used)")

    update_instance(instance_id, db_path, status="stopped", pid=None)
    audit_log.log_event(inst.id, inst.name, "stop", f"pid {pid} exited gracefully")
    return Result.success(message=f"Instance '{inst.name}' stopped")


def restart_instance(
    instance_id: str, database: str | None = None, db_path=None
) -> Result:
    """Stop then start. Recovering a crashed init needs explicit Start
    (restart passes no confirm callback, so it cleanly refuses there)."""
    from odoo_vite.core.registry import get_instance

    stop_res = stop_instance(instance_id, db_path=db_path)
    if not stop_res.ok:
        return Result.failure(f"Restart aborted: {stop_res.message}")
    inst = get_instance(instance_id, db_path)
    start_res = start_instance(
        instance_id, database=database or (inst.primary_db if inst else None),
        confirm_cb=None, db_path=db_path,
    )
    if not start_res.ok:
        return Result.failure(f"Restarted stop, but start failed: {start_res.message}")
    from odoo_vite.core import audit as _audit

    _audit.log_event(instance_id, (inst.name if inst else instance_id),
                     "restart", start_res.message)
    return Result.success(data=start_res.data,
                          message=f"Instance restarted ({start_res.message})")


def get_statuses(db_path=None) -> list[dict]:
    """Batch status for UI polling. Fast, never raises, self-heals stale rows."""
    from odoo_vite.core.registry import list_instances, update_instance

    try:
        instances = list_instances(db_path)
    except Exception:
        return []

    out: list[dict] = []
    for inst in instances:
        entry = {
            "id": inst.id, "name": inst.name, "status": inst.status,
            "pid": None, "port": inst.port, "version": inst.version,
            "cpu_percent": None, "memory_mb": None,
            "password_storage": inst.password_storage,
            "provisioning_mode": inst.provisioning_mode,
        }
        if (inst.status or "") != "running":
            out.append(entry)
            continue
        try:
            pid = _alive_pid(inst)
            if pid is None:
                update_instance(inst.id, db_path, status="stopped", pid=None)
                audit_log.log_event(
                    inst.id, inst.name, "process_gone",
                    "was cached running but the process is gone — marked stopped")
                entry["status"] = "stopped"
                out.append(entry)
                continue
            proc = psutil.Process(pid)
            with proc.oneshot():
                cpu = proc.cpu_percent(interval=None)
                rss = proc.memory_info().rss
            entry.update(pid=pid, cpu_percent=float(cpu or 0.0),
                         memory_mb=round(rss / 1048576, 1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                update_instance(inst.id, db_path, status="stopped", pid=None)
            except Exception:
                pass
            entry["status"] = "stopped"
        except Exception:
            pass  # keep the cached row on unexpected errors; next tick retries
        out.append(entry)
    return out


def switch_database(
    instance_id: str,
    new_db: str,
    confirm_cb: ConfirmCb | None = None,
    db_path=None,
) -> Result:
    """Switch which database a running instance serves (Sprint 4).

    Stopped instances are never surprise-started: if the instance is not
    running, this only records new_db as primary (equivalent to Set Primary).
    New-to-this-instance databases reuse Sprint 3's confirm-before-create
    flow (db_created is flipped False so start_instance -i base's it behind
    confirm_cb); existing databases start plainly with -d.
    """
    from odoo_vite.core import db_manager
    from odoo_vite.core.registry import get_instance, update_instance

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    target = (new_db or "").strip()
    if not db_manager.is_valid_identifier(target):
        return Result.failure(
            f"Invalid database name '{new_db}' "
            "(must match ^[a-z_][a-z0-9_]*$)")

    was_running = _alive_pid(inst) is not None
    if was_running:
        stop_res = stop_instance(instance_id, db_path=db_path)
        if not stop_res.ok:
            return Result.failure(f"Switch aborted: {stop_res.message}")
    else:
        update_instance(instance_id, db_path, primary_db=target)
        if target == (inst.primary_db or ""):
            return Result.success(message=f"'{target}' is already the primary database")
        return Result.success(
            data={"database": target, "started": False},
            message=f"Primary database set to '{target}' "
                    f"(instance was stopped — not started)")

    pw = _password_for(inst) or None
    ready = (db_manager.database_exists(target, inst.db_user, pw)
             and db_manager.database_initialized(target, inst.db_user, pw))
    if ready:
        # Known, initialized database: plain start with -d, no creation flow.
        start_res = start_instance(instance_id, database=target,
                                   confirm_cb=None, db_path=db_path)
    else:
        # New database: route through the first-start confirm + create flow.
        update_instance(instance_id, db_path, primary_db=target,
                        db_created=False)
        start_res = start_instance(instance_id, database=target,
                                   confirm_cb=confirm_cb, db_path=db_path)
        if not start_res.ok:
            return Result.failure(
                f"Switch to '{target}' failed: {start_res.message} "
                f"(primary recorded as '{target}', db_created reset — "
                "retrying will re-confirm creation)")
    if not start_res.ok:
        return Result.failure(f"Switch to '{target}' failed: {start_res.message}")
    return Result.success(
        data={**(start_res.data or {}), "database": target, "started": True},
        message=f"Instance now serving '{target}'")
