"""Instance export/import (U5.5): portable tar.gz bundles for migration.

Archive layout (top level):
  instance.json          manifest (settings + password, see below)
  files/<relpath>        instance tree MINUS venv/logs/caches (same subset
                         as clone — the venv is rebuilt after import)

Secrets: the manifest carries the resolved DB password because odoo.conf
inside files/ already contains it in plaintext — the bundle adds nothing
new. Archives are chmod 600 and the dialogs warn to treat them like a
database dump. Databases themselves are NOT bundled — move those with
Backup/Restore.

Imports are traversal-safe (absolute names and ".." rejected) and land in
a fresh unique folder with a new id/port. No GUI imports.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import uuid
from pathlib import Path

from odoo_vite.core.result import Result

MANIFEST_NAME = "instance.json"
FILES_PREFIX = "files/"
FORMAT = 1
ARCHIVE_MODE = 0o600

_SKIP_NAMES = {"venv", "logs", "__pycache__", ".mypy_cache", ".pytest_cache",
               ".ruff_cache", ".git"}
_SKIP_SUFFIXES = (".pyc", ".pyo")


def _say(progress_cb, line: str) -> None:
    """Progress line, tolerant of a missing callback (CLI/tests)."""
    if progress_cb is not None:
        progress_cb(line)


def _iter_tree(base: Path):
    """Yield (fs_path, arc_rel) for exportable entries under base."""
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_NAMES and not d.startswith(".git")]
        rdir = Path(root).relative_to(base)
        for name in files:
            if name.endswith(_SKIP_SUFFIXES):
                continue
            rel = (rdir / name) if str(rdir) != "." else Path(name)
            yield Path(root) / name, rel


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Validated members: no absolute paths, no '..', manifest present."""
    members = archive.getmembers()
    names = [m.name for m in members]
    if MANIFEST_NAME not in names:
        raise ValueError(f"not an Odoo Vite bundle ({MANIFEST_NAME} missing)")
    safe = []
    for member in members:
        parts = Path(member.name).parts
        if Path(member.name).is_absolute() or ".." in parts:
            raise ValueError(
                f"unsafe archive entry rejected: {member.name!r}")
        safe.append(member)
    return safe


def export_preview(archive_path: str | Path) -> Result:
    """Read the manifest without extracting (for the import dialog)."""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_members(archive)
            raw = archive.extractfile(MANIFEST_NAME)
            if raw is None:
                return Result.failure("Bundle manifest unreadable")
            manifest = json.loads(raw.read().decode("utf-8"))
        if manifest.get("format") != FORMAT:
            return Result.failure(
                f"Unsupported bundle format {manifest.get('format')!r} "
                f"(this app reads {FORMAT})")
        try:
            size = Path(archive_path).stat().st_size
        except OSError:
            size = 0
        return Result.success(
            data={"name": manifest.get("name", ""),
                  "version": manifest.get("version", ""),
                  "port": manifest.get("port", 8069),
                  "size_bytes": size},
            message=f"Bundle for '{manifest.get('name', '?')}'")
    except (tarfile.TarError, ValueError, OSError) as exc:
        return Result.failure(f"Cannot read bundle: {exc}")
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"Cannot read bundle: {exc}")


def export_instance(source_id: str, dest_file: str | Path, db_path=None,
                    src_password: str | None = None,
                    progress_cb=None, cancel=None) -> Result:
    """Write a portable bundle of an instance. Never raises.

    src_password: GUI-thread-resolved secret (see clone_instance).
    progress_cb/cancel: 3.2.0 streaming — coarse stage lines + a cancel
    check per file so a huge tree stays interruptible.
    """
    from odoo_vite.core import audit as audit_log
    from odoo_vite.core.registry import (
        get_db_password, get_instance,
    )
    from odoo_vite.core.version import __version__ as app_version

    try:
        source = get_instance(source_id, db_path)
        if source is None:
            return Result.failure(f"No instance with id '{source_id}'")
        if (source.status or "") == "running":
            return Result.failure(
                f"Stop '{source.name}' before exporting it")
        src_base = Path(source.path).expanduser() if source.path else None
        if src_base is None or not src_base.is_dir():
            return Result.failure(
                f"Source files for '{source.name}' are missing — "
                "nothing to export")
        dest = Path(dest_file).expanduser()
        if dest.suffix not in (".gz", ".tgz") and dest.suffixes[-2:] != [
                ".tar", ".gz"]:
            return Result.failure("Destination must end in .tar.gz")
        try:
            password = src_password if src_password is not None \
                else get_db_password(source)
        except Exception:
            password = src_password or ""

        manifest = {
            "format": FORMAT,
            "app_version": app_version,
            "name": source.name,
            "version": source.version,
            "mode": source.mode,
            "port": source.port,
            "db_user": source.db_user,
            "exported_password": password,
            "provisioning_mode": source.provisioning_mode,
            "description": source.description,
            "workers": source.workers,
            "log_level": source.log_level,
            "auto_update_modules": list(source.auto_update_modules or []),
            "pending_update_modules": list(
                source.pending_update_modules or []),
            "addons_state": list(source.addons_state or []),
            "src_base": str(src_base),
            "has_community": (src_base / "community").is_dir(),
            "enterprise_external": (
                "" if not source.enterprise_path
                or str(Path(source.enterprise_path).expanduser())
                .startswith(str(src_base))
                else source.enterprise_path),
        }
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _say(progress_cb, f"Packing '{source.name}'…")
            with tarfile.open(dest, "w:gz") as archive:
                payload = json.dumps(manifest, indent=2).encode("utf-8")
                info = tarfile.TarInfo(MANIFEST_NAME)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
                count = 0
                for fs_path, rel in _iter_tree(src_base):
                    if cancel is not None and cancel():
                        try:
                            dest.unlink(missing_ok=True)
                        except OSError:
                            pass
                        return Result.failure("Export cancelled")
                    archive.add(str(fs_path),
                                arcname=FILES_PREFIX + rel.as_posix(),
                                recursive=False)
                    count += 1
                    if count % 500 == 0:
                        _say(progress_cb, f"Packed {count} files…")
            os.chmod(dest, ARCHIVE_MODE)
        except OSError as exc:
            return Result.failure(f"Cannot write bundle: {exc}")
        try:
            audit_log.log_event(source_id, source.name, "export",
                                f"exported to {dest.name}")
        except Exception:
            pass
        return Result.success(
            data={"path": str(dest), "name": source.name},
            message=f"Exported '{source.name}' to {dest.name} "
                    f"(settings + files; databases NOT included)")
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"Export failed: {exc}")


def import_instance(archive_path: str | Path, new_name: str,
                    new_port: int | None = None, db_path=None,
                    allow_plaintext: bool = False,
                    progress_cb=None, cancel=None) -> Result:
    """Restore a bundle as a new instance. Never raises."""
    from odoo_vite.core import audit as audit_log
    from odoo_vite.core import provisioning as prov
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import (
        create_instance,
        delete_db_password,
        delete_instance,
        get_instance_by_name,
        store_db_password,
        update_instance,
    )

    try:
        name = (new_name or "").strip()
        if not name:
            return Result.failure("Import needs a name")
        if get_instance_by_name(name, db_path) is not None:
            return Result.failure(
                f"Instance '{name}' already exists "
                "(names are case-insensitive)")
        archive = Path(archive_path).expanduser()
        if not archive.is_file():
            return Result.failure(f"Bundle not found: {archive}")
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = _safe_members(tar)
                raw = tar.extractfile(MANIFEST_NAME)
                if raw is None:
                    return Result.failure("Bundle manifest unreadable")
                manifest = json.loads(raw.read().decode("utf-8"))
        except (tarfile.TarError, ValueError, OSError) as exc:
            return Result.failure(f"Cannot read bundle: {exc}")
        if manifest.get("format") != FORMAT:
            return Result.failure(
                f"Unsupported bundle format {manifest.get('format')!r}")
        if cancel is not None and cancel():
            return Result.failure("Import cancelled")

        if new_port is None:
            port = prov.suggest_port(int(manifest.get("port", 8069) or 8069)
                                     + 1, db_path)
        else:
            try:
                port = int(new_port)
            except (TypeError, ValueError):
                return Result.failure(f"Invalid port '{new_port}'")
            if not 1024 <= port <= 65535:
                return Result.failure(f"Port {port} out of range (1024–65535)")
            if not prov.is_port_free(port):
                return Result.failure(
                    f"Port {port} is already in use — pick another")

        dst_base = prov.unique_instance_path(name)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = _safe_members(tar)
                file_total = sum(
                    1 for m in members
                    if m.name.startswith(FILES_PREFIX) and m.isfile())
                _say(progress_cb, f"Unpacking {file_total} files…")
                done = 0
                for member in members:
                    # 3.2.0: cancel mid-extract — clean the partial tree so
                    # a retried import starts from a real fresh folder.
                    if cancel is not None and cancel():
                        shutil.rmtree(dst_base, ignore_errors=True)
                        return Result.failure("Import cancelled")
                    if member.name == MANIFEST_NAME:
                        continue
                    if not member.name.startswith(FILES_PREFIX):
                        continue
                    rel = member.name[len(FILES_PREFIX):]
                    if not rel:
                        continue
                    target = dst_base / rel
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            continue
                        with open(target, "wb") as fh:
                            fh.write(extracted.read())
                        done += 1
                        if done % 500 == 0:
                            _say(progress_cb, f"Unpacked {done}/{file_total}…")
                    # symlinks/devices/sockets: skipped by design
        except (tarfile.TarError, ValueError, OSError) as exc:
            return Result.failure(f"Cannot extract bundle: {exc}")
        (dst_base / "logs").mkdir(parents=True, exist_ok=True)

        old_base = str(manifest.get("src_base", ""))
        raw_addons = manifest.get("addons_state") or []

        def _remap(value: str) -> str:
            if value and old_base and value.startswith(old_base):
                return str(dst_base / Path(value).relative_to(old_base))
            return value

        addons_state = []
        for entry in raw_addons:
            if isinstance(entry, dict) and entry.get("path"):
                entry = dict(entry)
                entry["path"] = _remap(str(entry["path"]))
            addons_state.append(entry)

        enterprise = manifest.get("enterprise_external") or ""
        if enterprise and not Path(enterprise).expanduser().exists():
            enterprise = ""

        # last cancel point before anything touches the registry/keyring
        if cancel is not None and cancel():
            shutil.rmtree(dst_base, ignore_errors=True)
            return Result.failure("Import cancelled")
        _say(progress_cb, "Registering instance…")
        new_id = str(uuid.uuid4())
        storage, column = store_db_password(
            new_id, manifest.get("exported_password", "") or "",
            allow_plaintext=allow_plaintext)
        if storage == "unavailable":
            shutil.rmtree(dst_base, ignore_errors=True)
            return Result.failure(
                "OS keyring unavailable — cannot store the imported DB "
                "password. Install gnome-keyring or import with an explicit "
                "plaintext opt-out.")

        new_inst = Instance(
            id=new_id,
            name=name,
            version=manifest.get("version", ""),
            mode=manifest.get("mode", "managed") or "managed",
            path=str(dst_base),
            venv_path=str(dst_base / "venv"),
            community_path=str(dst_base / "community"),
            enterprise_path=enterprise or None,
            custom_addons_path="",
            conf_path="",
            log_path="",
            port=port,
            db_user=manifest.get("db_user", "odoo") or "odoo",
            db_password=column,
            password_storage=storage,
            primary_db="",
            tracked_dbs=[],
            auto_update_modules=list(
                manifest.get("auto_update_modules") or []),
            pending_update_modules=list(
                manifest.get("pending_update_modules") or []),
            status="stopped",
            pid=None,
            db_created=False,
            provisioning_mode=manifest.get("provisioning_mode",
                                           "developer") or "developer",
            description=manifest.get("description", ""),
            workers=int(manifest.get("workers", 0) or 0),
            log_level=manifest.get("log_level", "info") or "info",
            addons_state=addons_state,
        )
        res = create_instance(new_inst, db_path)
        if not res.ok:
            shutil.rmtree(dst_base, ignore_errors=True)
            try:
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
                f"Files imported but odoo.conf failed: {conf_res.message}")
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
            audit_log.log_event(new_id, name, "import",
                                f"imported from {archive.name}")
        except Exception:
            pass
        return Result.success(
            data={"id": new_id, "name": name, "port": port,
                  "path": str(dst_base)},
            message=f"Imported '{name}' (no databases; rebuild the venv "
                    f"before starting)")
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"Import failed: {exc}")
