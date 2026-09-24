"""Enterprise edition support (Sprint ENT).

- detect_enterprise(instance): tri-state — "valid" (path set and looks
  like an Enterprise checkout), "invalid" (path set but not recognizable),
  "community" (nothing configured). Single home for the concept; reuses
  addon_paths.looks_like_addons_folder + adopt.check_enterprise_match.
- load_enterprise(): clone a USER-PROVIDED git URL (their own licensed
  remote; auth is entirely their git/SSH setup — failures surface
  verbatim, never worked around, no credential handling here) via
  git_manager.clone_repo, then wire enterprise_path + addons_path through
  addon_paths.apply_addons_state. Odoo Vite never clones Enterprise on
  its own initiative and never vendors the code.
- unload_enterprise(): DISABLE the enterprise entry (keeps list position
  for later re-enable) + clear enterprise_path. Files on disk untouched.

No GTK imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from odoo_vite.core.adopt import check_enterprise_match
from odoo_vite.core.addon_paths import looks_like_addons_folder
from odoo_vite.core.registry import get_instance, update_instance
from odoo_vite.core.result import Result

STATES = ("valid", "invalid", "community")


def detect_enterprise(instance: Any) -> Result:
    """Tri-state Enterprise detection for one instance record."""
    path = ((getattr(instance, "enterprise_path", None) or "").strip()
            if instance is not None else "")
    version = (getattr(instance, "version", "") or "").strip()
    if not path:
        return Result.success(
            data={"state": "community", "path": "",
                  "enterprise_major": "", "match": None},
            message="Community only, no Enterprise configured")
    valid = looks_like_addons_folder(path)
    if not valid:
        return Result.success(
            data={"state": "invalid", "path": path,
                  "enterprise_major": "", "match": None},
            message=f"Enterprise path is set but not recognizable: {path}")
    try:
        info = check_enterprise_match(version, path)
    except Exception:
        info = {"enterprise_major": "", "match": None}
    major = info.get("enterprise_major") or ""
    match = info.get("match")
    if match is True:
        message = f"Enterprise addons {major} match Odoo {version}."
    elif match is False:
        message = (f"Enterprise addons {major} do NOT match Odoo {version}"
                   f" — verify compatibility.")
    else:
        message = "Enterprise addons set, version unknown — verify compatibility."
    return Result.success(
        data={"state": "valid", "path": path,
              "enterprise_major": major, "match": match},
        message=message)


def load_enterprise(instance_id: str, git_url: str, branch: str,
                    progress_cb=None, cancel=None,
                    db_path=None) -> Result:
    """Clone the user's Enterprise remote + wire it in (ENT.2).

    Destination is always `<instance.path>/enterprise`. Auth failures come
    back as plain git errors in the message — surfaced, not worked around.
    """
    from odoo_vite.core import addon_paths
    from odoo_vite.core.git_manager import clone_repo

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    url = (git_url or "").strip()
    if not url:
        return Result.failure("No git URL given — Enterprise needs your own "
                              "licensed remote (SSH or authenticated HTTPS)")
    branch = (branch or "").strip() or (inst.version or "").strip()
    if not branch:
        return Result.failure("No branch given and instance has no version")
    if not inst.path:
        return Result.failure("Instance records no path")
    dest = Path(inst.path).expanduser() / "enterprise"
    res = clone_repo(url, branch, dest, progress_cb=progress_cb,
                     cancel=cancel,
                     label=f"Enterprise {branch}")
    if not res.ok:
        return Result.failure(res.message, data=res.data)
    if not looks_like_addons_folder(str(dest)):
        return Result.failure(
            f"Cloned {branch} into {dest}, but it doesn't look like an "
            f"Enterprise checkout (no addon manifests) — path left "
            f"unwired; inspect the clone manually",
            data={"path": str(dest)})
    stored = update_instance(instance_id, db_path,
                             enterprise_path=str(dest))
    if not stored.ok:
        return Result.failure(
            f"Cloned to {dest} BUT registry update failed: {stored.message}")
    entries = addon_paths.get_addons_state(
        get_instance(instance_id, db_path))
    if not any(e.get("path") == str(dest) for e in entries):
        entries.append({"path": str(dest), "enabled": True})
    applied = addon_paths.apply_addons_state(instance_id, entries, db_path)
    if not applied.ok:
        return Result.failure(
            f"Cloned to {dest} and recorded, BUT addons_path wiring failed: "
            f"{applied.message}")
    return Result.success(
        data={"path": str(dest), "addons_path": applied.data.get("addons_path")},
        message=f"Enterprise {branch} loaded from your remote into {dest}")


def unload_enterprise(instance_id: str, db_path=None) -> Result:
    """Stop using Enterprise addons; files on disk untouched (ENT.3)."""
    from odoo_vite.core import addon_paths

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    if not (inst.enterprise_path or "").strip():
        return Result.failure("No Enterprise configured on this instance")
    entries = addon_paths.get_addons_state(inst)
    changed = False
    for entry in entries:
        if entry.get("path") == inst.enterprise_path and entry.get("enabled"):
            entry["enabled"] = False
            changed = True
    if changed:
        applied = addon_paths.apply_addons_state(
            instance_id, entries, db_path)
        if not applied.ok:
            return Result.failure(
                f"Could not rewrite addons_path: {applied.message} "
                f"(files untouched)")
    stored = update_instance(instance_id, db_path, enterprise_path="")
    if not stored.ok:
        return Result.failure(
            f"addons_path updated BUT registry clear failed: {stored.message}")
    return Result.success(
        message="Enterprise unloaded — this instance stops using Enterprise "
                "addons; the cloned files were not deleted")
