"""odoo.conf generation (Sprint 2, Ticket 2.5).

write_conf(instance) builds addons_path from community [+ enterprise]
[+ custom], ensures custom_addons/ + logs/odoo.log exist, and writes
<instance_path>/odoo.conf. No GTK imports.
"""

from __future__ import annotations

import configparser
import io
from pathlib import Path

from odoo_vite.core.result import Result

CONF_FILENAME = "odoo.conf"
CUSTOM_ADDONS_DIRNAME = "custom_addons"
LOG_RELATIVE = "logs/odoo.log"


def write_conf(instance) -> Result:  # type: ignore[no-untyped-def]
    """Write odoo.conf for a (draft or complete) instance.

    Effective paths fall back to <instance.path> conventions when the
    Instance fields are empty; the resolved paths are returned in
    Result.data so the caller can persist them to the registry.
    """
    from odoo_vite.core.registry import get_db_password

    try:
        base = Path(instance.path).expanduser()
        if not str(instance.path):
            return Result.failure("Instance has no path — cannot write odoo.conf")
        community = Path(instance.community_path) if instance.community_path else base / "community"
        custom_addons = (
            Path(instance.custom_addons_path)
            if instance.custom_addons_path
            else base / CUSTOM_ADDONS_DIRNAME
        )
        conf_path = Path(instance.conf_path) if instance.conf_path else base / CONF_FILENAME
        log_path = Path(instance.log_path) if instance.log_path else base / LOG_RELATIVE

        try:
            custom_addons.mkdir(parents=True, exist_ok=True)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if not log_path.exists():
                log_path.touch()
        except OSError as exc:
            return Result.failure(f"Cannot prepare instance folders: {exc}")

        addons = [str(community / "addons")]
        if instance.enterprise_path:
            addons.append(str(Path(instance.enterprise_path)))
        addons.append(str(custom_addons))

        password = get_db_password(instance)

        parser = configparser.ConfigParser()
        parser["options"] = {
            "addons_path": ",".join(addons),
            "db_host": "localhost",
            "db_port": "5432",
            "db_user": instance.db_user or "odoo",
            "db_password": password,
            "xmlrpc_port": str(instance.port or 8069),
            "logfile": str(log_path),
        }
        buf = io.StringIO()
        parser.write(buf)
        try:
            conf_path.write_text(buf.getvalue(), encoding="utf-8")
        except OSError as exc:
            return Result.failure(f"Cannot write {conf_path}: {exc}")

        return Result.success(
            data={
                "conf_path": str(conf_path),
                "log_path": str(log_path),
                "custom_addons_path": str(custom_addons),
                "addons_path": ",".join(addons),
            },
            message=f"odoo.conf written to {conf_path}",
        )
    except Exception as exc:  # never raise into the UI
        return Result.failure(f"conf generation failed: {exc}")
