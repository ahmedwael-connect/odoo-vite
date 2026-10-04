"""Instance cloning (U5.1): duplicate files + registry row, no DB copy.

Semantics (deliberately narrow, documented in the dialog too):
- Copies the instance directory (community, custom_addons, conf) to a new
  folder, EXCLUDING venv, logs, and bytecode caches. The venv is NOT copied
  (absolute paths inside would break); run Repair/re-provision on the clone
  before starting it. Start refuses with a clear message until then.
- Databases are NOT copied: the clone starts with no primary_db, no tracked
  DBs, db_created=False. Create or track databases after cloning.
- Adopted instances with paths outside their folder keep pointing at the
  original external paths (only paths under the source folder are remapped).
- Refuses to clone a running instance (stop it first).

No GUI imports.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from odoo_vite.core.result import Result

_COPY_IGNORE = shutil.ignore_patterns(
    "venv", "logs", "__pycache__", "*.pyc", "*.pyo", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
)


def _remap(value: str, src_base: Path, dst_base: Path) -> str:
    """Remap a path under src_base to dst_base; leave others untouched."""
    if not value:
        return value
    try:
        rel = Path(value).expanduser().relative_to(src_base)
        return str(dst_base / rel)
    except (ValueError, RuntimeError):
        return value


def _remap_addons_state(entries: list, src_base: Path, dst_base: Path) -> list:
    remapped = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path"):
            entry = dict(entry)
            entry["path"] = _remap(str(entry["path"]), src_base, dst_base)
        remapped.append(entry)
    return remapped


def clone_instance(source_id: str, new_name: str, new_port: int | None = None,
                   db_path=None, allow_plaintext: bool = False,
                   src_password: str | None = None) -> Result:
    """Duplicate an instance's files + registry row. Never raises.

    src_password: pre-resolved source DB password. The Qt layer resolves it
    on the GUI thread (Secret Service dbus calls from worker threads hang)
    and passes it in; when None, resolution happens here (fine for tests
    and the headless path).
    """
    from odoo_vite.core import audit as audit_log
    from odoo_vite.core import provisioning as prov
    from odoo_vite.core.registry import (
        create_instance,
        delete_instance,
        get_db_password,
        get_instance,
        get_instance_by_name,
        store_db_password,
        update_instance,
    )
    from odoo_vite.core.instance import Instance

    try:
        name = (new_name or "").strip()
        if not name:
            return Result.failure("Clone needs a name")
        source = get_instance(source_id, db_path)
        if source is None:
            return Result.failure(f"No instance with id '{source_id}'")
        if (source.status or "") == "running":
            return Result.failure(
                f"Stop '{source.name}' before cloning it")
        if get_instance_by_name(name, db_path) is not None:
            return Result.failure(
                f"Instance '{name}' already exists "
                "(names are case-insensitive)")
        src_base = Path(source.path).expanduser() if source.path else None
        if src_base is None or not src_base.is_dir():
            return Result.failure(
                f"Source files for '{source.name}' are missing "
                f"({source.path or 'no path'}) — nothing to clone")

        if new_port is None:
            port = prov.suggest_port((source.port or 8069) + 1, db_path)
        else:
            try:
                port = int(new_port)
            except (TypeError, ValueError):
                return Result.failure(f"Invalid port '{new_port}'")
            if not 1024 <= port <= 65535:
                return Result.failure(
                    f"Port {port} out of range (1024–65535)")
            if not prov.is_port_free(port):
                return Result.failure(
                    f"Port {port} is already in use — pick another")

        dst_base = prov.unique_instance_path(name)
        try:
            shutil.copytree(src_base, dst_base, symlinks=True,
                            ignore=_COPY_IGNORE)
        except OSError as exc:
            return Result.failure(f"Could not copy files: {exc}")
        (dst_base / "logs").mkdir(parents=True, exist_ok=True)

        new_id = str(uuid.uuid4())
        try:
            password = src_password if src_password is not None \
                else get_db_password(source)
        except Exception:
            password = src_password or ""
        storage, column = store_db_password(
            new_id, password, allow_plaintext=allow_plaintext)
        if storage == "unavailable":
            shutil.rmtree(dst_base, ignore_errors=True)
            return Result.failure(
                "OS keyring unavailable — cannot carry the DB password. "
                "Install gnome-keyring (password login, not auto-login) "
                "or clone with an explicit plaintext opt-out.")

        new_inst = Instance(
            id=new_id,
            name=name,
            version=source.version,
            mode=source.mode,
            path=str(dst_base),
            venv_path=str(dst_base / "venv"),
            community_path=_remap(source.community_path, src_base, dst_base)
            or str(dst_base / "community"),
            enterprise_path=(
                _remap(source.enterprise_path or "", src_base, dst_base)
                or None),
            custom_addons_path="",
            conf_path="",
            log_path="",
            port=port,
            db_user=source.db_user,
            db_password=column,
            password_storage=storage,
            primary_db="",
            tracked_dbs=[],
            auto_update_modules=list(source.auto_update_modules or []),
            pending_update_modules=list(source.pending_update_modules or []),
            status="stopped",
            pid=None,
            db_created=False,
            provisioning_mode=source.provisioning_mode,
            description=source.description,
            workers=source.workers,
            log_level=source.log_level,
            python_binary=_remap(
                source.python_binary or "", src_base, dst_base),
            addons_state=_remap_addons_state(
                list(source.addons_state or []), src_base, dst_base),
        )
        res = create_instance(new_inst, db_path)
        if not res.ok:
            shutil.rmtree(dst_base, ignore_errors=True)
            try:
                from odoo_vite.core.registry import delete_db_password
                delete_db_password(new_id)
            except Exception:
                pass
            return Result.failure(res.message)

        from odoo_vite.core.conf_writer import write_conf
        conf_res = write_conf(new_inst)
        if not conf_res.ok:
            delete_instance(new_id, db_path)
            shutil.rmtree(dst_base, ignore_errors=True)
            return Result.failure(
                f"Clone copied but odoo.conf failed: {conf_res.message}")
        try:
            update_instance(
                new_id, db_path,
                conf_path=conf_res.data.get("conf_path", ""),
                log_path=conf_res.data.get("log_path", ""),
                custom_addons_path=conf_res.data.get(
                    "custom_addons_path", ""))
        except Exception:
            pass
        try:
            audit_log.log_event(
                new_id, name, "clone", f"cloned from {source.name}")
        except Exception:
            pass
        return Result.success(
            data={"id": new_id, "name": name, "port": port,
                  "path": str(dst_base)},
            message=f"Cloned '{source.name}' → '{name}' (no databases "
                    f"copied; rebuild the venv before starting)")
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"Clone failed: {exc}")
