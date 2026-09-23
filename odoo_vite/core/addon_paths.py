"""Structured addons_path management (Sprint 7, Ticket 7.4).

The conf holds ONE comma string; the feature needs add/remove/toggle/
reorder. This module owns the structured form — an ordered list of
{path, enabled} — stored in the registry (`addons_state` column) as the
source of truth. The conf's `addons_path` string is DERIVED from it
(enabled entries, in order) through the validated write path, never the
other way around, so conf and registry cannot disagree.

Toggle = excluded from the derived string, reference kept (useful for
"which folder breaks this" debugging). Reorder = Up/Down buttons in the UI
(deliberately not drag-and-drop: fewer edge cases, same operation as Move —
Move is treated as synonymous, not a second mechanism).

No GTK imports.
"""

from __future__ import annotations

from pathlib import Path

from odoo_vite.core.result import Result


def parse_addons_string(addons_path: str) -> list[dict]:
    """One-time migration: conf string → structured list (all enabled)."""
    entries = []
    for part in (addons_path or "").split(","):
        part = part.strip()
        if part:
            entries.append({"path": part, "enabled": True})
    return entries


def derive_addons_path(entries: list[dict] | None) -> str:
    """Structured list → conf string (enabled only, order preserved)."""
    return ",".join(e["path"] for e in (entries or [])
                    if e.get("path") and e.get("enabled", True))


def looks_like_addons_folder(path: str) -> bool:
    """Sprint 2 enterprise-check heuristic, reused: any subdir with a manifest."""
    try:
        root = Path(path).expanduser()
        return root.is_dir() and any(
            (sub / "__manifest__.py").is_file()
            for sub in root.iterdir() if sub.is_dir())
    except OSError:
        return False


def get_addons_state(instance) -> list[dict]:  # type: ignore[no-untyped-def]
    """Stored list, or lazy one-time migration from the conf string."""
    stored = list(instance.addons_state or [])
    if stored:
        return [{"path": str(e.get("path", "")), "enabled": bool(e.get("enabled", True))}
                for e in stored if isinstance(e, dict) and e.get("path")]
    # Not yet migrated: parse the live conf string (all enabled, in order).
    from odoo_vite.core.conf_manager import parse_conf_file

    conf = Path(instance.conf_path) if instance.conf_path else None
    current = ""
    if conf is not None and conf.is_file():
        current = parse_conf_file(conf).get("options", {}).get("addons_path", "")
    return parse_addons_string(current)


def apply_addons_state(instance_id: str, entries: list[dict],  # type: ignore[no-untyped-def]
                       db_path=None) -> Result:
    """Persist the structured list AND rewrite the conf's addons_path.

    Conf first (validated path): if the write fails, the registry is left
    untouched. Registry second. Both or neither, in that order.
    """
    from odoo_vite.core import conf_manager
    from odoo_vite.core.registry import get_instance, update_instance

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    clean = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            return Result.failure(f"Invalid addons entry: {entry!r}")
        path = str(entry.get("path", "")).strip()
        if not path:
            return Result.failure("Addons entries must not have an empty path")
        clean.append({"path": path, "enabled": bool(entry.get("enabled", True))})
    if not inst.conf_path:
        return Result.failure("Instance records no conf path")
    derived = derive_addons_path(clean)
    if not derived:
        return Result.failure("Refusing to write an empty addons_path — "
                              "Odoo would boot with no addons at all")
    write = conf_manager.update_conf_keys(inst.conf_path, {"addons_path": derived})
    if not write.ok:
        return Result.failure(f"Conf write failed, registry untouched: {write.message}")
    stored = update_instance(instance_id, db_path, addons_state=clean)
    if not stored.ok:
        return Result.failure(
            f"Conf updated BUT registry write failed: {stored.message} "
            "(re-open the manager to reconcile)")
    return Result.success(
        data={"addons_path": derived, "entries": clean},
        message=f"Addons path updated ({len(clean)} entries, "
                f"{sum(1 for e in clean if e['enabled'])} enabled)")
