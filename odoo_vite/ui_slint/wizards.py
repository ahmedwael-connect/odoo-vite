"""Wizard operations (PSS-7a Create; PSS-7b Adopt; PSS-7c Scaffold):
async core drivers for Slint page-stack wizards.

Mirrors the Qt wizard contracts (ui_slint must not import ui_qt):
page validation is pure and UI-thread-safe; long work runs async with
UI-thread sinks; draft safety stays in core.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from odoo_vite.core import adopt, git_manager, provisioning, system_check
from odoo_vite.core.db_manager import is_valid_identifier
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import get_instance, get_instance_by_name
from odoo_vite.core.result import Result


def generate_password(length: int = 20) -> str:
    """Strong random password (Qt _generate_password parity, no 0/O/1/l)."""
    alphabet = ("abcdefghjkmnpqrstuvwxyz"
                "ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_branches(data) -> list[str]:
    """list_odoo_branches returns a bare LIST (Qt A.2 parity) — accept
    both shapes, never leave the list empty while the count reads fine."""
    if isinstance(data, dict):
        return list(data.get("branches", []) or [])
    return list(data or [])


def validate_details(values: dict) -> str | None:
    """Details-page gate (Qt validatePage parity). Returns the error text,
    or None when provisioning may start."""
    name = str(values.get("name", "") or "").strip()
    if not name:
        return "Instance name is required."
    if get_instance_by_name(name) is not None:
        return f"An instance named '{name}' already exists."
    try:
        port = int(values.get("port", 0) or 0)
    except (TypeError, ValueError):
        return "Port must be a number."
    if not provisioning.is_port_free(port):
        return f"Port {port} is already in use — pick a free one."
    if not str(values.get("db_user", "") or "").strip():
        return "Database user is required."
    if not values.get("db_password") and not values.get("plaintext"):
        return "Set a password or explicitly opt out."
    db_name = str(values.get("db_name", "") or "").strip()
    if not db_name or not is_valid_identifier(db_name):
        return "Database name must be a valid identifier."
    return None


def build_draft(details: dict, version: str) -> Instance:
    """Fresh draft (Qt _build_instance parity); Retry keeps id + path and
    refreshes only the mutable fields — see refresh_draft()."""
    path = provisioning.unique_instance_path(details["name"] or "odoo")
    return Instance(
        name=details["name"], version=version, mode="managed",
        path=str(path), venv_path=str(path / "venv"),
        community_path=str(path / "community"),
        enterprise_path="",
        custom_addons_path=str(path / "custom_addons"),
        conf_path=str(path / "odoo.conf"),
        log_path=str(path / "logs" / "odoo.log"),
        port=int(details["port"]),
        db_user=details["db_user"] or "odoo",
        db_password=details["db_password"],
        password_storage="plaintext",  # resolved at register
        primary_db=details["db_name"],
        tracked_dbs=[details["db_name"]] if details["db_name"] else [],
        status="draft")


def refresh_draft(inst: Instance, details: dict, version: str) -> Instance:
    """Retry path: id + path stable, mutable fields refreshed."""
    inst.version = version or inst.version
    inst.port = int(details["port"])
    inst.db_user = details["db_user"] or "odoo"
    inst.db_password = details["db_password"]
    inst.primary_db = details["db_name"]
    inst.tracked_dbs = [details["db_name"]] if details["db_name"] else []
    return inst


class WizardOps:
    def __init__(self, on_message=None, on_branches=None,
                 on_syscheck=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._branches = on_branches or (lambda _b, _m: None)
        self._syscheck = on_syscheck or (lambda _c, _m: None)

    async def load_branches(self, search: str = ""):
        res = await asyncio.to_thread(
            git_manager.list_odoo_branches, search or "")
        branches = normalize_branches((res.data if res.ok else None))
        if not res.ok:
            self._message(f"Branch list failed: {res.message}", "error")
        self._branches(branches, res.message if res.ok else "")
        return branches

    async def run_syscheck(self, version: str):
        res = await asyncio.to_thread(
            system_check.check_requirements, version or "")
        checks = (res.data or {}).get("checks", []) if res.ok else []
        self._syscheck(checks, res.message)
        return checks

    async def provision(self, inst: Instance, plaintext: bool,
                        progress_cb=None, cancel=None):
        return await asyncio.to_thread(
            provisioning.provision_instance, inst,
            progress_cb=progress_cb, cancel=cancel,
            allow_plaintext=bool(plaintext))

    async def discard_draft(self, instance_id: str):
        return await asyncio.to_thread(
            provisioning.discard_draft, instance_id)

    async def adopt_run(self, name: str, conf: str, community: str,
                        overrides: dict):
        return await asyncio.to_thread(
            adopt.adopt_instance, name, conf, community,
            overrides=overrides)

    async def scaffold_install(self, definition: dict, dest: str,
                               instance_id: str, db_name: str,
                               progress_cb=None, cancel=None):
        """Generate, then install through the real install flow (Qt
        acceptance parity — a module isn't done until it installs)."""
        from odoo_vite.core import module_manager, module_scaffolder
        from odoo_vite.core.db_state import get_db_state
        from odoo_vite.core.registry import get_db_password

        inst = get_instance(instance_id)
        if inst is None:
            return Result.failure("Instance disappeared")

        def _emit(line: str) -> None:
            if progress_cb is not None:
                try:
                    progress_cb(line)
                except Exception:
                    pass

        def _work():
            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            try:
                exists = bool(get_db_state(
                    db_name, inst.db_user, pw).exists)
            except Exception as exc:
                return Result.failure(f"Cannot inspect '{db_name}': {exc}")
            if not exists:
                return Result.failure(
                    f"Database '{db_name}' does not exist — create it "
                    "first (Databases tab → Init). Acceptance installs "
                    "for real; it never auto-creates.")
            gen = module_scaffolder.scaffold(definition, dest)
            if not gen.ok:
                return Result.failure(f"Scaffold failed: {gen.message}")
            tech = definition.get("technical_name", "")
            res = module_manager.install_modules(
                inst, db_name, [tech], progress_cb=_emit, cancel=cancel)
            if not res.ok:
                return Result.failure(
                    "Generated OK, but acceptance install failed: "
                    f"{res.message}")
            return Result.success(
                message=f"Generated {tech} and installed cleanly into "
                        f"'{db_name}'")

        return await asyncio.to_thread(_work)


FIELD_TYPES = ["char", "text", "integer", "float", "boolean", "date",
               "datetime", "html"]


def parse_field_lines(text: str) -> list[dict]:
    """'name:type' per line (Qt definition() parity — unknown types and
    malformed lines are skipped, never error)."""
    fields = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, ftype = [p.strip() for p in line.split(":", 1)]
        if name and ftype in FIELD_TYPES:
            fields.append({"name": name, "type": ftype})
    return fields


def build_scaffold_definition(values: dict) -> dict:
    """Definition dict from form values (Qt definition() parity)."""
    fields = parse_field_lines(values.get("fields_text", ""))
    models = []
    if str(values.get("model", "") or "").strip():
        models = [{"name": values["model"].strip(),
                   "description": values.get("pretty", "").strip()
                   or values.get("tech", "").strip(),
                   "fields": fields}]
    return {"technical_name": values.get("tech", "").strip(),
            "pretty_name": values.get("pretty", "").strip(),
            "odoo_version": values.get("version", "").strip() or "17.0",
            "summary": values.get("summary", "").strip(),
            "author": values.get("author", "").strip(),
            "models": models}


def validate_scaffold(values: dict, has_instances: bool) -> str | None:
    """Definition-page gate minus the PG check (Qt validatePage parity —
    the database-exists check runs in the worker before generating)."""
    import re

    if not re.match(r"^[a-z_][a-z0-9_]*$",
                    str(values.get("tech", "") or "").strip()):
        return "Technical name must match ^[a-z_][a-z0-9_]*$."
    if not str(values.get("dest", "") or "").strip():
        return "Pick a destination folder."
    if not has_instances:
        return "No instance available for acceptance."
    if not str(values.get("db", "") or "").strip():
        return "Acceptance database is required."
    return None


GAP_FIELDS = (
    ("Addons path", "addons_path"),
    ("DB user", "db_user"),
    ("DB password", "db_password"),
    ("Port", "port"),
    ("Log file", "logfile"),
)


def parse_adopt_paths(conf: str, community: str) -> dict:
    """Live reparse for the Locate page (Qt _reparse parity): parsed conf,
    lenient validation report, detected version, and the status line."""
    conf = (conf or "").strip()
    community = (community or "").strip()
    parsed = adopt.parse_conf(conf) if conf else {}
    report = adopt.validate_adopted_conf(parsed)
    version = adopt.detect_version(community) if community else ""
    bits = []
    if conf:
        missing = [f for f, s in report.items() if s == "missing"]
        bits.append("conf parsed" + (
            f" (missing: {', '.join(missing)})" if missing else " ✓"))
    if community:
        ok = (Path(community) / "odoo-bin").is_file()
        bits.append(f"odoo-bin {'found' if ok else 'NOT FOUND'}"
                    + (f", version {version}" if version else ""))
    return {"parsed": parsed, "report": report, "version": version,
            "detected": " · ".join(bits)}


def validate_locate(name: str, conf: str, community: str) -> str | None:
    """Locate-page gate (Qt validatePage parity)."""
    name = (name or "").strip()
    if not name:
        return "Name is required."
    if get_instance_by_name(name) is not None:
        return f"An instance named '{name}' already exists."
    if not (conf or "").strip() or not Path(conf.strip()).is_file():
        return "Pick a readable odoo.conf first."
    if not (community or "").strip() or not (
            Path(community.strip()) / "odoo-bin").is_file():
        return "Community folder must contain odoo-bin."
    return None


def gap_rows(parsed: dict, report: dict) -> list[dict]:
    """Gap form rows (Qt GapsPage parity): one row per field with status;
    missing ones carry an editable entry."""
    parsed = parsed or {}
    report = report or {}
    port = parsed.get("xmlrpc_port") or parsed.get("http_port") or ""
    values = {"addons_path": parsed.get("addons_path", ""),
              "db_user": parsed.get("db_user", ""),
              "db_password": parsed.get("db_password", ""),
              "port": port,
              "logfile": parsed.get("logfile", "")}
    rows = []
    for caption, key in GAP_FIELDS:
        missing = report.get(key) == "missing"
        rows.append({"key": key, "caption": caption,
                     "status": ("MISSING — fill in:" if missing else
                                f"present: {values[key]}"[:80]),
                     "missing": missing,
                     "value": values[key] if missing else ""})
    return rows


def suggest_db_name(name: str) -> str:
    """Qt GapsPage parity: slugified default for the database entry."""
    return provisioning.slugify_db_name(name or "odoo")


def build_adopt_overrides(parsed: dict, gap_values: dict,
                          db_name: str) -> dict:
    """Adopt payload (Qt AdoptRunPage parity): primary db + filled gaps +
    enterprise split. Adopt never writes files."""
    overrides = {"primary_db": db_name}
    overrides.update({k: v for k, v in (gap_values or {}).items()
                      if str(v or "").strip()})
    parts = adopt.split_addons((parsed or {}).get("addons_path", ""))
    if parts.get("enterprise"):
        overrides["enterprise_path"] = parts["enterprise"]
    return overrides
