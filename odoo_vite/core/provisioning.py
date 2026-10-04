"""Provisioning orchestration (Sprint 2, Ticket 2.7).

provision_instance(instance, ...) is the single function the wizard's Step 6
calls in a background thread. Order with early-exit on first failure:

  register(draft) → clone → venv → pip → conf → role → finalize(stopped)

Retry resumes at the failed step: every expensive step self-skips when its
output already exists (community/.git, venv/bin/python, .pip-done sentinel),
so re-calling provision_instance after a failure only redoes what is missing.
Cancellation kills the running subprocess (see core/proc.py) and leaves the
draft row with last_error set, ready for Retry or Discard.

Also home to the small Step-3 form helpers: suggest_port/is_port_free,
slugify_db_name, default/unique instance paths. No GTK imports.
"""

from __future__ import annotations

import re
import shutil
import socket
from collections.abc import Callable
from pathlib import Path

from odoo_vite.core import events
from odoo_vite.core.result import Result
PIP_DONE_SENTINEL = ".odoo-vite-pip-done"

STEPS = ["register", "clone", "venv", "pip", "conf", "role", "finalize"]


# ---------------------------------------------------------------------------
# Step-3 form helpers

def is_port_free(port: int) -> bool:
    """True if nothing on this machine is listening on 127.0.0.1:port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(port)))
        return True
    except (OSError, ValueError, OverflowError):
        return False


def suggest_port(start: int = 8069, db_path=None) -> int:
    """Next free port ≥ start, skipping registry ports + live-bound ports."""
    from odoo_vite.core.registry import list_instances

    try:
        used = {int(inst.port) for inst in list_instances(db_path)}
    except Exception:
        used = set()
    port = max(int(start), 1024)
    while port <= 65535:
        if port not in used and is_port_free(port):
            return port
        port += 1
    return int(start)


def slugify_db_name(name: str) -> str:
    """'Odoo Client A' → 'odoo_client_a' (Postgres-identifier safe)."""
    slug = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        return "odoo"
    if slug[0].isdigit():
        slug = "odoo_" + slug
    return slug


def default_instance_path(name: str) -> Path:
    base = Path.home() / ".local" / "share" / "odoo-vite" / "instances"
    return base / (slugify_db_name(name) or "odoo")


def unique_instance_path(name: str) -> Path:
    """default_instance_path, with -2/-3/... suffix while the dir exists."""
    candidate = default_instance_path(name)
    index = 2
    while candidate.exists():
        candidate = candidate.parent / f"{candidate.name}-{index}"
        index += 1
    return candidate


# ---------------------------------------------------------------------------
# Orchestration

def _emit(progress_cb: Callable[[str], None] | None, line: str) -> None:
    events.emit("provision-log", line)
    if progress_cb is not None:
        try:
            progress_cb(line)
        except Exception:
            pass


def _fail(instance_id: str, step: str, message: str, db_path=None) -> Result:
    from odoo_vite.core.registry import update_instance

    try:
        update_instance(instance_id, db_path, status="draft", last_error=message)
    except Exception:
        pass
    return Result.failure(
        message, data={"failed_step": step, "instance_id": instance_id}
    )


def provision_instance(
    instance,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    db_path=None,
    allow_plaintext: bool = False,
) -> Result:
    """Run the full provisioning pipeline (background-thread entry point).

    allow_plaintext=True records an explicit, user-confirmed opt-out into
    plaintext password storage (H.2) and is audit-logged. Without it, a
    missing keyring aborts registration loudly instead of degrading.
    """
    from odoo_vite.core import conf_writer, db_manager, git_manager, venv_manager
    from odoo_vite.core.registry import (
        create_instance,
        get_db_password,
        get_instance,
        store_db_password,
        update_instance,
    )

    total = 6  # user-visible stages (register/finalize are instant)

    # -- resolve conventional paths -----------------------------------------
    if not instance.path:
        return Result.failure("Instance has no target folder (path is empty)")
    base = Path(instance.path).expanduser()
    if not instance.community_path:
        instance.community_path = str(base / "community")
    if not instance.venv_path:
        instance.venv_path = str(base / venv_manager.VENV_DIRNAME)
    community = Path(instance.community_path)
    venv = Path(instance.venv_path)

    # -- step 1: draft row ----------------------------------------------------
    existing = None
    try:
        existing = get_instance(instance.id, db_path)
    except Exception:
        existing = None
    if existing is not None and (existing.status or "") == "stopped":
        return Result.success(
            data={"instance_id": instance.id, "failed_step": None},
            message=f"Instance '{instance.name}' already provisioned",
        )
    secret = instance.db_password or ""
    storage, column_value = store_db_password(
        instance.id, secret, allow_plaintext=allow_plaintext)
    if storage == "unavailable":
        msg = ("No working OS keyring found — refusing to store the database "
               "password in plaintext. Enable a Secret Service (e.g. "
               "'sudo apt install gnome-keyring', then log out and back in) "
               "or explicitly tick the plaintext opt-out in the wizard.")
        return Result.failure(
            msg, data={"failed_step": "register", "instance_id": instance.id})
    if storage == "plaintext":
        from odoo_vite.core import audit as audit_log

        try:
            audit_log.log_event(instance.id, instance.name, "plaintext_opt_out",
                                "user explicitly opted into plaintext password storage")
        except Exception:
            pass
    instance.password_storage = storage
    instance.db_password = column_value  # "" when keyring holds the secret
    instance.status = "draft"
    if existing is None:
        # H.1: stamp the privilege posture in force at creation time.
        from odoo_vite.core.settings import get_provisioning_mode

        try:
            instance.provisioning_mode = get_provisioning_mode(db_path)
        except Exception:
            instance.provisioning_mode = "developer"
        res = create_instance(instance, db_path)
        if not res.ok:
            return Result.failure(
                f"Cannot register draft instance: {res.message}",
                data={"failed_step": "register", "instance_id": instance.id},
            )
        _emit(progress_cb, f"Registered draft instance '{instance.name}'")
    else:
        update_instance(
            instance.id, db_path,
            status="draft", last_error=None,
            password_storage=storage, db_password=column_value,
            path=instance.path, venv_path=instance.venv_path,
            community_path=instance.community_path,
            version=instance.version, port=instance.port,
            db_user=instance.db_user, primary_db=instance.primary_db,
        )
        _emit(progress_cb, f"Resuming draft instance '{instance.name}'")

    real_password = get_db_password(instance) or secret

    # -- step 2: clone ---------------------------------------------------------
    _emit(progress_cb, f"=== [1/{total}] Cloning Odoo {instance.version} ===")
    if (community / ".git").is_dir() and (community / "odoo-bin").is_file():
        _emit(progress_cb, "Community repo already cloned — skipping")
    else:
        res = git_manager.clone_instance(instance.version, base, progress_cb=lambda ln: _emit(progress_cb, ln), cancel=cancel)
        if not res.ok:
            if (res.data or {}).get("cancelled"):
                return _fail(instance.id, "clone", f"Provisioning cancelled during clone. {res.message}", db_path)
            return _fail(instance.id, "clone", res.message, db_path)

    # -- step 3: venv ----------------------------------------------------------
    _emit(progress_cb, f"=== [2/{total}] Creating Python venv ===")
    if (venv / "bin" / "python").exists():
        _emit(progress_cb, "Venv already exists — skipping")
    else:
        res = venv_manager.create_venv(base, progress_cb=lambda ln: _emit(progress_cb, ln), cancel=cancel)
        if not res.ok:
            if (res.data or {}).get("cancelled"):
                return _fail(instance.id, "venv", f"Provisioning cancelled during venv creation. {res.message}", db_path)
            return _fail(instance.id, "venv", res.message, db_path)

    # -- step 4: pip ------------------------------------------------------------
    _emit(progress_cb, f"=== [3/{total}] Installing Odoo requirements (slow) ===")
    sentinel = base / PIP_DONE_SENTINEL
    if sentinel.is_file() and (venv / "bin" / "python").exists():
        _emit(progress_cb, "Requirements already installed — skipping")
    else:
        res = venv_manager.install_requirements(
            venv, community,
            progress_cb=lambda ln: _emit(progress_cb, ln), cancel=cancel,
        )
        if not res.ok:
            if (res.data or {}).get("cancelled"):
                return _fail(instance.id, "pip", f"Provisioning cancelled during pip install. {res.message}", db_path)
            return _fail(instance.id, "pip", res.message, db_path)
        try:
            sentinel.write_text("ok\n", encoding="utf-8")
        except OSError:
            pass

    # -- step 5: conf ------------------------------------------------------------
    _emit(progress_cb, f"=== [4/{total}] Writing odoo.conf ===")
    res = conf_writer.write_conf(instance)
    if not res.ok:
        return _fail(instance.id, "conf", res.message, db_path)
    update_instance(
        instance.id, db_path,
        conf_path=(res.data or {}).get("conf_path", instance.conf_path),
        log_path=(res.data or {}).get("log_path", instance.log_path),
        custom_addons_path=(res.data or {}).get("custom_addons_path", instance.custom_addons_path),
    )
    _emit(progress_cb, res.message)

    # -- step 6: postgres role -----------------------------------------------------
    _emit(progress_cb, f"=== [5/{total}] Ensuring Postgres role ===")
    res = db_manager.ensure_role(instance.db_user, real_password)
    if not res.ok:
        return _fail(instance.id, "role", res.message, db_path)
    _emit(progress_cb, res.message)

    # -- finalize ------------------------------------------------------------------
    update_instance(instance.id, db_path, status="stopped", last_error=None)
    _emit(progress_cb, f"=== [6/{total}] Done — '{instance.name}' ready ===")
    return Result.success(
        data={"instance_id": instance.id, "failed_step": None},
        message=f"Instance '{instance.name}' provisioned and ready",
    )


def discard_draft(instance_id: str, db_path=None) -> Result:
    """Delete partial files + keyring entry + registry row for a draft.

    3.3.0 P1: the wizard can only discard its own unfinished draft. Any
    other id (adopted instance = the user's real checkout, or a
    finalized/stopped instance) must never reach the rmtree below.
    """
    from odoo_vite.core.registry import delete_db_password, delete_instance, get_instance

    inst = None
    try:
        inst = get_instance(instance_id, db_path)
    except Exception:
        inst = None
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    mode = (inst.mode or "managed").lower()
    status = (inst.status or "").lower()
    if mode == "adopted" or status != "draft":
        return Result.failure(
            f"Refusing to discard '{inst.name}': only unfinished managed "
            "drafts are deleted here (adopted instances keep their files; "
            "finalized instances are removed via Remove)")
    if inst.path:
        try:
            shutil.rmtree(inst.path, ignore_errors=True)
        except Exception:
            pass
    delete_db_password(instance_id)
    res = delete_instance(instance_id, db_path)
    if not res.ok:
        return Result.failure(f"Discard failed: {res.message}")
    return Result.success(
        data={"instance_id": instance_id}, message="Draft discarded"
    )
