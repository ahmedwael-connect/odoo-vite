"""Adopt existing Odoo installs (Sprint 4, Ticket 4.1).

- parse_conf: ini [options] → plain dict (RawConfigParser: passwords may
  contain %; interpolation must not mangle them).
- validate_adopted_conf: {field: present|missing} for the required-for-us
  set. Lenient per PM: never raises, never invents values — missing fields
  surface as editable inputs in the UI.
- detect_version: parse odoo/release.py (deterministic, no subprocess —
  chosen over `odoo-bin --version`, which needs a working Python env).
- adopt_instance: build a mode="adopted" row merged from conf + overrides.
  Touches NO files (no clone/venv/conf-write). Status comes from an
  immediate live-process scan (adopted Odoo may already be running).

No GTK imports.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import psutil

from odoo_vite.core.instance import Instance
from odoo_vite.core.result import Result

REQUIRED_FIELDS = ("addons_path", "db_user", "db_password", "port", "logfile")
PORT_KEYS = ("xmlrpc_port", "http_port")


def parse_conf(conf_path: str | Path) -> dict:
    """Parse an Odoo conf file's [options] into a plain dict.

    Sprint 7 consolidation: delegates to conf_manager.parse_conf_file (single
    shared parser); behavior unchanged (options dict, {} on any error).
    """
    from odoo_vite.core.conf_manager import parse_conf_file

    try:
        return parse_conf_file(conf_path).get("options", {})
    except Exception:
        return {}


def validate_adopted_conf(parsed: dict) -> dict:
    """Report {field: 'present'|'missing'} for each required-for-us field.

    db_password counts as present even when blank (peer-auth setups).
    Port counts as present when either xmlrpc_port or http_port exists.
    """
    parsed = parsed or {}
    report = {
        "addons_path": "present" if parsed.get("addons_path") else "missing",
        "db_user": "present" if parsed.get("db_user") else "missing",
        "db_password": "present" if "db_password" in parsed else "missing",
        "port": "present" if (parsed.get("xmlrpc_port") or parsed.get("http_port")) else "missing",
        "logfile": "present" if parsed.get("logfile") else "missing",
    }
    return report


def detect_version(community_path: str | Path) -> str:
    """Read the Odoo version from <community>/odoo/release.py. '' if unknown."""
    try:
        text = (Path(community_path) / "odoo" / "release.py").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(
        r"version_info\s*=\s*\(\s*(\d+)\s*,\s*(\d+)", text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return ""


def split_addons(addons_path: str) -> dict:
    """Heuristic split of a comma-separated addons_path.

    Returns {"community_addons": ..., "enterprise": ..., "custom": ...}.
    Community is recognised by a trailing '/addons' (or '/odoo/addons');
    enterprise by an 'enterprise' path segment; the rest is custom.
    Missing pieces are ''.
    """
    out = {"community_addons": "", "enterprise": "", "custom": ""}
    customs: list[str] = []
    for entry in (addons_path or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        low = entry.lower()
        if low.endswith("/addons") or low.endswith("/odoo/addons"):
            out["community_addons"] = out["community_addons"] or entry
        elif "enterprise" in low:
            out["enterprise"] = out["enterprise"] or entry
        else:
            customs.append(entry)
    out["custom"] = ",".join(customs)
    return out


def find_live_pid(conf_path: str, community_path: str = "") -> int | None:
    """PID of a running odoo-bin using this conf (None when not running)."""
    conf = (conf_path or "").strip()
    community = (community_path or "").strip()
    if not conf and not community:
        return None
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                joined = " ".join(proc.info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if "odoo-bin" not in joined:
                continue
            if (conf and conf in joined) or (community and community in joined):
                return int(proc.info["pid"])
    except Exception:
        return None
    return None


def adopt_instance(
    name: str,
    conf_path: str | Path,
    community_path: str | Path,
    overrides: dict | None = None,
    db_path=None,
    allow_plaintext: bool = False,
) -> Result:
    """Register an existing install as mode='adopted'. Touches no files."""
    from odoo_vite.core.registry import (
        create_instance,
        get_db_password,
        get_instance_by_name,
        store_db_password,
    )

    overrides = dict(overrides or {})
    name = (name or "").strip()
    if not name:
        return Result.failure("Instance name is required")
    if get_instance_by_name(name, db_path) is not None:
        return Result.failure(f"An instance named '{name}' already exists")

    conf = str(conf_path or "")
    community = str(community_path or "")
    if not conf or not Path(conf).is_file():
        return Result.failure(f"Odoo conf file not found: {conf or '(none given)'}")
    if not community or not (Path(community) / "odoo-bin").is_file():
        return Result.failure(
            f"odoo-bin not found under: {community or '(none given)'}")
    venv_given = str((overrides or {}).get("venv_path") or "")
    if venv_given and not (Path(venv_given) / "bin" / "python").is_file():
        return Result.failure(
            f"Venv python not found at {venv_given}/bin/python — point at a "
            "virtualenv folder (or leave empty and set it later)")

    parsed = parse_conf(conf)
    port_raw = (overrides.get("port") or parsed.get("xmlrpc_port")
                or parsed.get("http_port") or "8069")
    try:
        port = int(str(port_raw).strip())
    except ValueError:
        return Result.failure(f"Invalid port value '{port_raw}' in conf/overrides")
    if not (1 <= port <= 65535):
        return Result.failure(f"Port {port} out of range")

    def _pick(key: str, default: str = "") -> str:
        value = overrides.get(key, parsed.get(key, default))
        return str(value or default)

    primary_db = str(overrides.get("primary_db") or "").strip()
    if not primary_db:
        return Result.failure(
            "Primary database is required (Odoo conf files don't record a "
            "default database — enter it to adopt)")

    inst = Instance(
        name=name,
        version=str(overrides.get("version") or detect_version(community) or ""),
        mode="adopted",
        path=str(overrides.get("path") or str(Path(conf).parent)),
        venv_path=str(overrides.get("venv_path") or ""),
        community_path=community,
        enterprise_path=overrides.get("enterprise_path") or None,
        custom_addons_path=str(overrides.get("custom_addons_path") or ""),
        conf_path=conf,
        log_path=_pick("logfile"),
        port=port,
        db_user=_pick("db_user", "odoo"),
        db_password=_pick("db_password", ""),
        password_storage="plaintext",
        primary_db=primary_db,
        tracked_dbs=[primary_db],
        status="stopped",
    )

    # Secrets deserve the keyring even for adopted rows — fail loudly
    # without it unless the user explicitly opted out (H.2).
    storage, column = store_db_password(inst.id, inst.db_password,
                                        allow_plaintext=allow_plaintext)
    if storage == "unavailable":
        return Result.failure(
            "No working OS keyring found — refusing to store the database "
            "password in plaintext. Enable a Secret Service (e.g. "
            "'sudo apt install gnome-keyring', then log out and back in) "
            "or explicitly tick the plaintext opt-out.")
    if storage == "plaintext":
        from odoo_vite.core import audit as audit_log

        try:
            audit_log.log_event(inst.id, inst.name, "plaintext_opt_out",
                                "user explicitly opted into plaintext password storage")
        except Exception:
            pass
    inst.password_storage = storage
    inst.db_password = column

    # Live check: an adopted instance may already be running.
    live_pid = find_live_pid(conf, community)
    if live_pid is not None:
        inst.status = "running"
        inst.pid = live_pid

    # If its database already exists *and is initialized*, future Starts
    # skip the create flow; otherwise the first start offers creation.
    # Part A: single ground-truth read (not two independent probes).
    try:
        from odoo_vite.core.db_state import get_db_state

        pw = get_db_password(inst) or _pick("db_password", "")
        _state = get_db_state(primary_db, inst.db_user, pw or None)
        inst.db_created = bool(_state.exists and _state.initialized)
    except Exception:
        inst.db_created = False

    res = create_instance(inst, db_path)
    if not res.ok:
        return Result.failure(f"Cannot register adopted instance: {res.message}")

    from odoo_vite.core import audit as audit_log

    audit_log.log_event(inst.id, inst.name, "adopt",
                        f"conf={conf} live={'yes pid ' + str(live_pid) if live_pid else 'no'}")
    state = f"running (pid {live_pid})" if live_pid else "stopped"
    return Result.success(
        data={"id": inst.id, "status": inst.status, "pid": live_pid,
              "version": inst.version, "db_created": inst.db_created},
        message=f"Instance '{name}' adopted ({state})",
    )


# ---------------------------------------------------------------------------
# Enterprise version-mismatch check (Phase 1.5, Ticket H.5)

def manifest_version(manifest_path: str | Path) -> str:
    """Read the 'version' key from an Odoo __manifest__.py ('17.0.1.0.0' style)."""
    try:
        tree = ast.parse(Path(manifest_path).read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            try:
                data = ast.literal_eval(node)
            except (ValueError, SyntaxError):
                continue
            if isinstance(data, dict) and data.get("version"):
                return str(data["version"])
    return ""


def enterprise_major(version_str: str) -> str:
    """'17.0.1.0.0' → '17.0' ('' when unparseable)."""
    match = re.match(r"\s*(\d+\.\d+)", str(version_str or ""))
    return match.group(1) if match else ""


def sample_enterprise_version(enterprise_path: str | Path) -> str:
    """Full version string from the first readable addon manifest ('' if none)."""
    try:
        entries = sorted(Path(enterprise_path).iterdir())
    except OSError:
        return ""
    for sub in entries:
        if not sub.is_dir():
            continue
        manifest = sub / "__manifest__.py"
        if manifest.is_file():
            version = manifest_version(manifest)
            if version:
                return version
    return ""


def check_enterprise_match(instance_version: str,
                           enterprise_path: str | Path | None) -> dict:
    """Compare enterprise addons against the Odoo version (H.5).

    Returns {"checked": bool, "enterprise_version": str,
             "enterprise_major": str, "match": True|False|None}.
    match=None means "unknown" (no enterprise path or no version found) —
    the UI shows a warning only on a definite mismatch, never on unknown.
    """
    if not enterprise_path:
        return {"checked": False, "enterprise_version": "",
                "enterprise_major": "", "match": None}
    full = sample_enterprise_version(enterprise_path)
    major = enterprise_major(full)
    if not major or not (instance_version or "").strip():
        return {"checked": True, "enterprise_version": full,
                "enterprise_major": major, "match": None}
    return {"checked": True, "enterprise_version": full,
            "enterprise_major": major,
            "match": major == instance_version.strip()}
