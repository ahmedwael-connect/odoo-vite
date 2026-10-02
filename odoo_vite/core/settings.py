"""App-level settings (Phase 1.5, Ticket H.1).

Stored in a `settings` key/value table inside the same registry SQLite DB
(chosen over a separate file: single backup/cleanup story, no new paths).
Currently: provisioning_mode = "developer" (default, CREATEDB role) or
"managed" (least-privilege role; DB create/drop are explicit privileged ops).

No GTK imports.
"""

from __future__ import annotations

from odoo_vite.core.result import Result

DEFAULTS = {
    "provisioning_mode": "developer",
    "theme": "system",
}

VALID_MODES = ("developer", "managed")
VALID_THEMES = ("dark", "light", "system")


def get_setting(key: str, default: str | None = None, db_path=None) -> str | None:
    """Read a setting (DEFAULTS → stored value → explicit default)."""
    from odoo_vite.core.registry import _connect

    fallback = DEFAULTS.get(key, default)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else fallback
    except Exception:
        return fallback


def set_setting(key: str, value: str, db_path=None) -> Result:
    """Write a setting (validates provisioning_mode)."""
    from odoo_vite.core.registry import _connect

    if key == "provisioning_mode" and value not in VALID_MODES:
        return Result.failure(
            f"Invalid provisioning mode '{value}' (expected one of {VALID_MODES})")
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        return Result.success(data={"key": key, "value": value},
                              message=f"Setting '{key}' saved")
    except Exception as exc:
        return Result.failure(f"Cannot save setting '{key}': {exc}")


def get_provisioning_mode(db_path=None) -> str:
    """Global default mode for newly created instances (sanitized)."""
    mode = get_setting("provisioning_mode", "developer", db_path) or "developer"
    return mode if mode in VALID_MODES else "developer"


def set_provisioning_mode(mode: str, db_path=None) -> Result:
    return set_setting("provisioning_mode", mode, db_path)


def get_theme(db_path=None) -> str:
    """UI theme choice: dark | light | system (browser storage under
    WebKitGTK is ephemeral, so the settings table is the source of truth)."""
    theme = get_setting("theme", "system", db_path) or "system"
    return theme if theme in VALID_THEMES else "system"


def set_theme(theme: str, db_path=None) -> Result:
    if theme not in VALID_THEMES:
        return Result.failure(
            f"Invalid theme '{theme}' (expected one of {VALID_THEMES})")
    return set_setting("theme", theme, db_path)
