"""odoo.conf management (Sprint 7): read / validated-update / backup.

Three rules (spec, apply to every ticket here):
1. Never write a conf that doesn't parse or that empties a value Odoo
   relies on — validate BEFORE writing; refuse with specifics.
2. Every overwrite first copies the previous file to `odoo.conf.bak`
   (one rolling backup) + `restore_conf_backup()` gives the one-click way back.
3. Editing a RUNNING instance never live-reloads it — the UI labels every
   save "takes effect on next restart"; core stays silent on that (UI copy).

Unknown sections ([queue_job], …) are read AND preserved on save — never
silently dropped. No GTK imports.
"""

from __future__ import annotations

import configparser
import shutil
from pathlib import Path

from odoo_vite.core.result import Result

BACKUP_SUFFIX = ".bak"

# Changed values for these keys must be non-empty (emptying a live value is
# the classic way to produce an unbootable conf). Pre-existing values are
# never forced — adopted sparse confs (e.g. peer-auth without db_password)
# stay valid; only the edit itself is policed.
NON_EMPTY_IF_CHANGED = {
    "addons_path", "db_user", "db_host", "xmlrpc_port", "http_port",
    "db_port", "logfile", "workers", "log_level",
}
INT_KEYS = {"xmlrpc_port", "http_port", "db_port", "workers",
            "longpolling_port", "gevent_port", "max_cron_threads"}


def parse_conf_file(conf_path: str | Path) -> dict:
    """Shared ini parser (Sprint 7 consolidation — adopt.parse_conf delegates).

    Returns {section: {key: value}} preserving file order. Never raises
    (empty dict on any error); RawConfigParser so % in passwords survives.
    """
    try:
        parser = configparser.RawConfigParser()
        parser.optionxform = str  # keep original key case
        parser.read(str(conf_path), encoding="utf-8")
        return {section: dict(parser.items(section))
                for section in parser.sections()}
    except Exception:
        return {}


def read_conf(conf_path: str | Path) -> Result:
    """Read a conf file: data={options, sections, path}."""
    path = Path(conf_path).expanduser() if str(conf_path) else None
    if path is None or not path.is_file():
        return Result.failure(f"Conf file not found: {conf_path or '(none given)'}")
    sections = parse_conf_file(path)
    if "options" not in sections:
        return Result.failure(f"No [options] section in {path}")
    return Result.success(
        data={"options": sections["options"], "sections": sections,
              "path": str(path)},
        message=f"Read {len(sections['options'])} option(s) from {path.name}")


def _serialize(sections: dict) -> str:
    import io

    parser = configparser.RawConfigParser()
    parser.optionxform = str
    for section, items in sections.items():
        parser.add_section(section)
        for key, value in items.items():
            parser.set(section, key, value)
    buf = io.StringIO()
    parser.write(buf)
    return buf.getvalue()


def update_conf_keys(conf_path: str | Path, changes: dict) -> Result:
    """Validated write: backup → apply → re-parse → save.

    changes values: None DELETES the key; anything else is set (str()).
    Refuses (leaving the file untouched, no backup taken) when the result
    wouldn't parse, when a changed key would be emptied, or when a changed
    numeric key isn't numeric.
    """
    path = Path(conf_path).expanduser()
    if not path.is_file():
        return Result.failure(f"Conf file not found: {path}")
    if not isinstance(changes, dict) or not changes:
        return Result.failure("No changes supplied")
    current = parse_conf_file(path)
    if "options" not in current:
        return Result.failure(f"No [options] section in {path} — refusing to guess")

    options = dict(current["options"])
    for key, value in changes.items():
        key = str(key)
        if value is None:
            options.pop(key, None)
            continue
        text = str(value)
        if key in NON_EMPTY_IF_CHANGED and not text.strip():
            return Result.failure(
                f"Refusing to empty '{key}' — Odoo relies on a value here")
        if key in INT_KEYS:
            try:
                number = int(text.strip())
            except ValueError:
                return Result.failure(
                    f"Refusing to set '{key}' to '{text}' — must be a number")
            if key.endswith("port") and not (1 <= number <= 65535):
                return Result.failure(
                    f"Refusing to set '{key}' to '{text}' — port out of range")
            if key == "workers" and number < 0:
                return Result.failure(
                    f"Refusing to set 'workers' to '{text}' — must be >= 0")
        options[key] = text

    candidate = dict(current)
    candidate["options"] = options
    try:
        text_out = _serialize(candidate)
        verify = configparser.RawConfigParser()
        verify.optionxform = str
        verify.read_string(text_out)
        if not verify.has_section("options"):
            raise ValueError("re-parse lost [options]")
    except Exception as exc:
        return Result.failure(f"Refusing to write an unparseable conf: {exc}")

    backup = path.parent / (path.name + BACKUP_SUFFIX)
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        return Result.failure(f"Cannot back up {path} first — aborting: {exc}")
    try:
        path.write_text(text_out, encoding="utf-8")
    except OSError as exc:
        return Result.failure(f"Cannot write {path} (backup kept at {backup}): {exc}")
    return Result.success(
        data={"path": str(path), "backup": str(backup),
              "changed": sorted(str(k) for k in changes)},
        message=f"Saved {len(changes)} change(s) to {path.name} (backup kept)")


def conf_backup_info(conf_path: str | Path) -> dict | None:
    """Metadata about the rolling backup, or None when there isn't one."""
    path = Path(conf_path).expanduser()
    backup = path.parent / (path.name + BACKUP_SUFFIX)
    try:
        stat = backup.stat()
    except OSError:
        return None
    from datetime import datetime, timezone

    return {"path": str(backup), "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}


def restore_conf_backup(conf_path: str | Path) -> Result:
    """One-click way back: validated restore of odoo.conf.bak over the conf."""
    path = Path(conf_path).expanduser()
    backup = path.parent / (path.name + BACKUP_SUFFIX)
    if not backup.is_file():
        return Result.failure("No backup to restore (no odoo.conf.bak found)")
    probe = parse_conf_file(backup)
    if "options" not in probe:
        return Result.failure("Backup itself is unparseable — refusing to restore it")
    try:
        # keep a backup-of-the-current too, so restore is itself undoable
        second = path.parent / (path.name + ".pre-restore-bak")
        if path.is_file():
            shutil.copy2(path, second)
        shutil.copy2(backup, path)
    except OSError as exc:
        return Result.failure(f"Restore failed: {exc}")
    return Result.success(
        data={"path": str(path)},
        message=f"Restored {path.name} from backup")


def regenerate_conf(instance) -> Result:  # type: ignore[no-untyped-def]
    """Rebuild [options] from registry fields, preserving unknown sections.

    Ticket 7.3's explicit action: fresh options via conf_writer.write_conf,
    then unknown sections ([queue_job], …) merged back from the pre-read so
    regeneration never silently drops what it doesn't manage.
    """
    from odoo_vite.core import conf_writer

    conf_path = Path(instance.conf_path) if instance.conf_path else None
    if conf_path is None:
        return Result.failure("Instance records no conf path")
    previous = parse_conf_file(conf_path) if conf_path.is_file() else {}
    if conf_path.is_file():
        try:
            shutil.copy2(conf_path, conf_path.parent / (conf_path.name + BACKUP_SUFFIX))
        except OSError as exc:
            return Result.failure(f"Cannot back up before regenerating: {exc}")
    res = conf_writer.write_conf(instance)
    if not res.ok:
        return res
    extras = {s: items for s, items in previous.items() if s != "options"}
    if not extras:
        return Result.success(data={**(res.data or {}), "preserved_sections": []},
                              message=(res.message or "") + " (no extra sections)")
    merged = parse_conf_file(conf_path)
    merged.update(extras)
    try:
        conf_path.write_text(_serialize(merged), encoding="utf-8")
    except OSError as exc:
        return Result.failure(f"Regenerated options but failed merging sections: {exc}")
    return Result.success(
        data={**(res.data or {}), "preserved_sections": sorted(extras)},
        message=(res.message or "") + f" (preserved {sorted(extras)})")
