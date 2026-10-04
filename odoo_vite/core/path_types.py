"""Addon-path type detection (3.2.0 F2).

Each `addons_state` entry carries a `type` key classifying what the path
is, so the UI (and future tooling) can tell a core tree from an enterprise
checkout from the user's own folders:

  community   core addons (community_path itself, or its addons trees)
  enterprise  enterprise checkout (enterprise_path, or a folder that
              ships the web_enterprise addon)
  custom      the instance's own custom-addons folder(s)
  extra       any other addons folder the user wired into addons_path
  unknown     not classified (empty path, or nothing matched)

Heuristic only — detection never raises and never blocks a write: every
probe is guarded so a deleted/unreadable folder degrades to a plain path
comparison. Classification is informational (labels, auto-typing); the
addons_path derivation itself never consults it.
"""

from __future__ import annotations

from pathlib import Path

PATH_TYPES = ("community", "enterprise", "custom", "extra", "unknown")


def normalize(path) -> str:
    """Absolute-ish form for comparisons (never raises)."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return str(Path(raw).expanduser())


def _same(a, b) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def detect_path_type(instance, path: str) -> str:  # type: ignore[no-untyped-def]
    """Classify one addons path for this instance. Returns a PATH_TYPES
    member; anything unexpected degrades to "unknown"."""
    raw = str(path or "").strip()
    if not raw:
        return "unknown"
    try:
        return _detect(instance, raw)
    except Exception:  # noqa: BLE001 — detection must never break a caller
        return "unknown"


def _detect(instance, path: str) -> str:
    community = str(getattr(instance, "community_path", "") or "")
    if _same(path, community):
        return "community"
    if community:
        if _same(path, Path(community) / "addons") or _same(
                path, Path(community) / "odoo" / "addons"):
            return "community"
    try:
        if (Path(normalize(path)) / "odoo-bin").is_file():
            return "community"
    except OSError:
        pass

    enterprise = str(getattr(instance, "enterprise_path", "") or "")
    if enterprise and _same(path, enterprise):
        return "enterprise"
    try:
        root = Path(path).expanduser()
        if root.is_dir() and (root / "web_enterprise"
                              / "__manifest__.py").is_file():
            return "enterprise"
    except OSError:
        pass

    custom_raw = str(getattr(instance, "custom_addons_path", "") or "")
    if any(_same(path, part.strip())
           for part in custom_raw.split(",") if part.strip()):
        return "custom"
    base = str(getattr(instance, "path", "") or "")
    if base and _same(path, Path(base) / "custom_addons"):
        return "custom"

    from odoo_vite.core.addon_paths import looks_like_addons_folder

    return "extra" if looks_like_addons_folder(path) else "unknown"


def detect_types(instance, entries) -> list[dict]:  # type: ignore[no-untyped-def]
    """Copy of `entries` with `type` re-detected on every row."""
    typed: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["type"] = detect_path_type(instance, row.get("path", ""))
        typed.append(row)
    return typed
