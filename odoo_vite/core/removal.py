"""Instance removal (Sprint 4, Ticket 4.3).

- Managed: stop if running → drop primary DB (only if requested) →
  delete files → delete row. Only the primary_db is dropped (it is the one
  the Remove dialog names); tracked-but-not-primary databases are left alone
  — noted choice, see Sprint 4 report.
- Adopted: stop if running (safety, same as managed), then delete the
  registry row ONLY. drop_db=True on adopted is a loud no-op, never silent.
- Keyring entry is cleaned up in both cases. Audit "remove" either way.

No GTK imports.
"""

from __future__ import annotations

import shutil

from odoo_vite.core.result import Result


def remove_instance(instance_id: str, drop_db: bool = False, db_path=None,
                    drop_extra_dbs: list[str] | None = None) -> Result:
    """Remove (managed) or unregister (adopted) an instance.

    drop_db drops the primary_db; drop_extra_dbs drops additional databases,
    each of which must be in the instance's tracked_dbs (never anything
    unassociated — refused loudly before anything is touched).
    """
    from odoo_vite.core import audit as audit_log
    from odoo_vite.core import db_manager, process_manager
    from odoo_vite.core.registry import (
        delete_db_password,
        delete_instance,
        get_db_password,
        get_instance,
    )

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")

    mode = (inst.mode or "managed").lower()
    notes: list[str] = []

    # Never delete files / drop a DB from under a running process.
    if (inst.status or "") == "running":
        stop_res = process_manager.stop_instance(instance_id, db_path=db_path)
        if not stop_res.ok:
            return Result.failure(
                f"Removal aborted: could not stop '{inst.name}' "
                f"({stop_res.message})")
        notes.append("stopped first")

    if mode == "adopted":
        if drop_db or drop_extra_dbs:
            notes.append("drop_db ignored — adopted: Odoo Vite does not "
                         "manage its files or data")
        delete_db_password(instance_id)
        del_res = delete_instance(instance_id, db_path)
        if not del_res.ok:
            return Result.failure(f"Unregister failed: {del_res.message}")
        audit_log.log_event(inst.id, inst.name, "remove",
                            "adopted unregistered; files+db untouched. "
                            + "; ".join(notes))
        message = (f"'{inst.name}' removed from Odoo Vite. "
                   "Its files and database were not touched.")
        if notes:
            message += " (" + "; ".join(notes) + ")"
        return Result.success(data={"mode": "adopted", "files_deleted": False,
                                    "db_dropped": False},
                              message=message)

    # Managed from here on.
    wanted = ([inst.primary_db] if drop_db and inst.primary_db else [])
    for extra in drop_extra_dbs or []:
        if extra and extra not in wanted:
            wanted.append(extra)
    associated = set(inst.tracked_dbs or []) | ({inst.primary_db} if inst.primary_db else set())
    refused = [d for d in wanted if d not in associated]
    if refused:
        return Result.failure(
            f"Removal aborted: refusing to drop database(s) not associated "
            f"with this instance: {', '.join(refused)}")

    dropped: list[str] = []
    if wanted:
        pw = None
        try:
            pw = get_db_password(inst) or None
        except Exception:
            pw = None
        # H-B3: ground truth before dropping — a database that was never
        # created (e.g. H-B1 crashed mid--i base) is "nothing to drop", not a
        # reason to abort the whole removal. But if Postgres itself is
        # unreachable we cannot tell missing from unknown, so abort loudly
        # instead of risking orphaned databases.
        if not db_manager.server_reachable():
            return Result.failure(
                "Removal aborted: PostgreSQL is unreachable, so database "
                "drops cannot be verified — start Postgres and retry "
                "(files and registry row left intact)")
        # Part A: one ground-truth read per database (no per-caller logic).
        from odoo_vite.core.db_state import get_db_state

        for db_name in wanted:
            if not get_db_state(db_name, inst.db_user, pw).exists:
                notes.append(f"database '{db_name}' did not exist — nothing to drop")
                continue
            drop_res = db_manager.drop_database(db_name, inst.db_user, pw)
            if not drop_res.ok:
                return Result.failure(
                    f"Removal aborted: {drop_res.message} "
                    "(files and registry row left intact)")
            dropped.append(db_name)
        if dropped:
            notes.append(f"database(s) dropped: {', '.join(dropped)}")
    db_dropped = bool(dropped)

    files_deleted = False
    if inst.path:
        try:
            shutil.rmtree(inst.path, ignore_errors=False)
            files_deleted = True
            notes.append(f"folder {inst.path} deleted")
        except FileNotFoundError:
            notes.append(f"folder {inst.path} already gone")
        except OSError as exc:
            return Result.failure(
                f"Removal aborted: cannot delete {inst.path} ({exc}) "
                "(registry row left intact)")

    delete_db_password(instance_id)
    del_res = delete_instance(instance_id, db_path)
    if not del_res.ok:
        return Result.failure(
            f"Files removed but registry cleanup failed: {del_res.message}")

    audit_log.log_event(inst.id, inst.name, "remove",
                        f"managed removed; db_dropped={db_dropped}. "
                        + "; ".join(notes))
    return Result.success(
        data={"mode": "managed", "files_deleted": files_deleted,
              "db_dropped": db_dropped},
        message=f"Instance '{inst.name}' removed. " + "; ".join(notes))
