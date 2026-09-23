"""Dev-tools export helpers (Sprint 10, Tickets 10.5–10.6).

launch.json: pure file generation. debugpy wiring answer: attach-mode needs
the instance's odoo-bin launched UNDER debugpy (e.g. `debugpy --listen
5678 <venv>/bin/python <community>/odoo-bin -c <conf>`). Our launcher does
NOT do that today — adding a "launch with debug support" toggle to
process_manager is a small, explicit follow-up (flagged, not snuck in).
The generated file documents this itself so the config never misleads.

Editor detection reuses the Sprint 1 system_check spirit (PATH probing),
not a second mechanism. No GTK imports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from odoo_vite.core.result import Result

DEBUG_PORT = 5678


def generate_launch_json(instance, debug_port: int = DEBUG_PORT,  # type: ignore[no-untyped-def]
                         dest_dir: str | Path | None = None) -> Result:
    """Write .vscode/launch.json (debugpy attach) for an instance."""
    try:
        port = int(debug_port or DEBUG_PORT)
    except (TypeError, ValueError):
        return Result.failure(f"Invalid debug port '{debug_port}'")
    if not 1 <= port <= 65535:
        return Result.failure(f"Debug port {port} out of range")
    community = (instance.community_path or "").strip()
    if not community:
        return Result.failure("Instance records no community path")
    base = Path(dest_dir).expanduser() if dest_dir else Path(
        (instance.path or "").strip())
    if not str(base):
        return Result.failure("No destination folder (no instance path)")
    config = {
        "version": "0.2.0",
        "configurations": [{
            "name": f"Odoo Vite: {instance.name} (attach)",
            "type": "debugpy",
            "request": "attach",
            "connect": {"host": "localhost", "port": port},
            "pathMappings": [{"localRoot": community,
                              "remoteRoot": community}],
            "justMyCode": False,
            "comment": (
                "ATTACH MODE: this only connects if the instance was started "
                "UNDER debugpy, which Odoo Vite does not do automatically. "
                "Either start it manually as: "
                f"debugpy --listen {port} {instance.venv_path}/bin/python "
                f"{community}/odoo-bin -c {instance.conf_path} "
                f"- or ask for the planned 'launch with debug support' toggle. "
                f"Odoo conf: {instance.conf_path} - DB: {instance.primary_db}"),
        }],
    }
    try:
        vscode = base / ".vscode"
        vscode.mkdir(parents=True, exist_ok=True)
        dest = vscode / "launch.json"
        dest.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    except OSError as exc:
        return Result.failure(f"Cannot write launch.json: {exc}")
    return Result.success(
        data={"path": str(dest), "port": port},
        message=f"launch.json written to {dest}")


def detect_editors() -> dict:
    """PATH probe for VS Code / Cursor (Sprint 1 detection style)."""
    found = {}
    for editor in ("code", "cursor"):
        path = shutil.which(editor)
        if path:
            found[editor] = path
    return found


def open_in_editor(editor: str, folder: str | Path) -> Result:
    """Fire-and-forget `code|cursor <folder>`. Never blocks, never raises."""
    if editor not in ("code", "cursor"):
        return Result.failure(f"Unknown editor '{editor}' (expected code/cursor)")
    binary = shutil.which(editor)
    if binary is None:
        return Result.failure(
            f"'{editor}' was not found on PATH — install it first, then retry")
    folder = str(folder or "").strip()
    if not folder:
        return Result.failure("No folder to open")
    if not Path(folder).is_dir():
        return Result.failure(f"Folder does not exist: {folder}")
    try:
        subprocess.Popen([binary, folder], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return Result.failure(f"Cannot launch {editor}: {exc}")
    return Result.success(data={"editor": editor, "folder": folder},
                          message=f"Opened {folder} in {editor}")
