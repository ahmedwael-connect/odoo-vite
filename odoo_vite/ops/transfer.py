"""Transfer operations (PSS-8): export/import bundles + preferences.

Mirrors Qt's lifecycle export_bundle/import_dialog + preferences
contracts (odoo_vite.ops must not import ui_qt):

- Export/import run async with message + refresh sinks (trees can be
  GBs — never on the UI thread). Keyring resolves on the caller thread
  (Qt dbus rule, same as clone). Bundle permission 600 is core's
  (transfer.ARCHIVE_MODE) — asserted in tests, not reimplemented.
- Preferences are fully synchronous (Qt parity: cheap settings +
  keyring-probe reads, instant write); the bridge does them inline, so
  they need no ops class — helpers live here for testability.
- Scope honesty (Qt parity): bundles carry files + settings only —
  databases travel via Backup/Restore, the venv rebuilds.
"""

from __future__ import annotations

import asyncio

from odoo_vite.core import transfer as transfer_core
from odoo_vite.core.registry import (
    get_db_password,
    get_db_path,
    get_instance,
    keyring_available,
)
from odoo_vite.core.result import Result
from odoo_vite.core.settings import (
    get_provisioning_mode,
    set_provisioning_mode,
)

PROVISIONING_MODES = ["developer", "managed"]
MODE_LABELS = ["Developer (default)", "Managed (least privilege)"]
MODE_NOTES = [
    "Postgres roles get CREATEDB — creating databases just works.",
    "Least-privilege roles — DB create/drop are explicit, privileged "
    "operations.",
]


def read_preferences() -> dict:
    """Synchronous environment snapshot for the dialog (Qt parity —
    never raises; degrades to safe defaults)."""
    try:
        mode = get_provisioning_mode()
    except Exception:
        mode = "developer"
    try:
        kr_available = bool(keyring_available())
    except Exception:
        kr_available = False
    try:
        db_path = str(get_db_path())
    except Exception:
        db_path = ""
    if kr_available:
        kr_text = "OS keyring: available — passwords stored securely."
    else:
        kr_text = ("OS keyring: NOT available — install gnome-keyring "
                   "(password login, not auto-login) or opt out to "
                   "plaintext explicitly at creation time.")
    return {"mode": mode, "keyring_text": kr_text, "db_path": db_path}


def save_preferences(mode: str) -> Result:
    """Persist the provisioning mode (applies to new instances)."""
    if mode not in PROVISIONING_MODES:
        return Result.failure(f"Unknown mode '{mode}'")
    return set_provisioning_mode(mode)


def bundle_filename(name: str, stamp: str) -> str:
    """Qt _export_pick parity: slug + UTC stamp + .tar.gz."""
    slug = "".join(c if c.isalnum() else "_" for c in (name or "instance"))
    return f"{slug}_{stamp}.tar.gz"


class TransferOps:
    def __init__(self, on_message=None, on_refresh=None) -> None:
        self._message = on_message or (lambda _m: None)
        self._refresh = on_refresh or (lambda: None)

    def preview(self, archive: str):
        """Bundle manifest read (fast local tar open — Qt did this
        synchronously too)."""
        return transfer_core.export_preview(archive)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    async def export_bundle(self, instance_id: str, dest: str):
        """Password resolves HERE (UI thread) — dbus never on workers."""
        inst = get_instance(instance_id)
        if inst is None:
            res = Result.failure(f"No instance with id '{instance_id}'")
            self._message(res.message, "error")
            return res
        try:
            password = get_db_password(inst) or ""
        except Exception:
            password = ""
        self._message(f"Exporting '{inst.name}'…", "info")
        return await self._run(
            transfer_core.export_instance, instance_id, dest,
            src_password=password)

    async def import_bundle(self, archive: str, new_name: str,
                            new_port: int | None):
        if not (new_name or "").strip():
            self._message("Import needs a name — cancelled", "error")
            return Result.failure("Import needs a name")
        self._message(f"Importing '{new_name}'…", "info")
        return await self._run(
            transfer_core.import_instance, archive,
            new_name.strip(), new_port)
