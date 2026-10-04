"""Instance dataclass/model (§1.2 of the Phase 1 charter).

Dependency-light: dataclasses + stdlib json only. No ORM, no GTK.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def effective_python(instance: "Instance") -> str:
    """Interpreter command for an instance (Sprint 7.5 consolidation).

    Explicit python_binary override wins (adopted/unusual setups); otherwise
    the venv's own python. Single place — start/initialize/install/update/
    uninstall/shell must all agree instead of each building the path.
    """
    override = (instance.python_binary or "").strip()
    if override:
        return override
    venv = (instance.venv_path or "").strip()
    return f"{venv}/bin/python" if venv else ""


@dataclass
class Instance:
    """One managed or adopted Odoo instance (maps 1:1 to the `instances` table)."""

    name: str
    version: str = ""
    mode: str = "managed"  # 'managed' | 'adopted'
    path: str = ""
    venv_path: str = ""
    community_path: str = ""
    enterprise_path: str | None = None
    custom_addons_path: str = ""
    conf_path: str = ""
    log_path: str = ""
    port: int = 8069
    db_user: str = "odoo"
    # Sprint 2 (PM answers): keyring primary, SQLite plaintext fallback.
    # password_storage tracks which ("keyring" | "plaintext"); db_password holds
    # the secret ONLY in the plaintext fallback (else "").
    db_password: str = ""
    password_storage: str = "plaintext"
    primary_db: str = ""
    tracked_dbs: list[str] = field(default_factory=list)
    auto_update_modules: list[str] = field(default_factory=list)
    # 3.2.0 F1: one-shot update queue — merged into the next start's -u
    # argument and cleared when that launch succeeds (auto_update_modules
    # above stays the "every start" list).
    pending_update_modules: list[str] = field(default_factory=list)
    # Sprint 2: "draft" until provisioning succeeds, then "stopped".
    # (Live truth still comes from process_manager.)
    status: str = "draft"
    pid: int | None = None
    # Sprint 3: first-start DB creation happened (independent of status, so a
    # later Stop/Start cycle never re-triggers the create-DB confirmation).
    db_created: bool = False
    # Phase 1.5 H.1: privilege posture at creation ("developer" | "managed").
    # Legacy rows (NULL) read as "developer" — never auto-migrated.
    provisioning_mode: str = "developer"
    # Sprint 7.5 metadata: description is registry-only; workers/log_level
    # mirror real odoo.conf keys; python_binary is an explicit interpreter
    # override (empty = venv's own python — the normal case).
    description: str = ""
    workers: int = 0
    log_level: str = "info"
    python_binary: str = ""
    # Sprint 7.4: structured addons list [{path, enabled}] (JSON). Empty =
    # not yet migrated (migrated lazily from the conf's addons_path string).
    addons_state: list = field(default_factory=list)
    # Sprint 2: latest provisioning failure, so the wizard can offer Resume/Discard.
    last_error: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_row(self) -> dict:
        """Map to a SQLite row dict (JSON-encoding the list columns)."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "mode": self.mode,
            "path": self.path,
            "venv_path": self.venv_path,
            "community_path": self.community_path,
            "enterprise_path": self.enterprise_path,
            "custom_addons_path": self.custom_addons_path,
            "conf_path": self.conf_path,
            "log_path": self.log_path,
            "port": self.port,
            "db_user": self.db_user,
            "db_password": self.db_password,
            "password_storage": self.password_storage,
            "primary_db": self.primary_db,
            "tracked_dbs": json.dumps(self.tracked_dbs or []),
            "auto_update_modules": json.dumps(self.auto_update_modules or []),
            "pending_update_modules": json.dumps(
                self.pending_update_modules or []),
            "status": self.status,
            "pid": self.pid,
            "last_error": self.last_error,
            "db_created": 1 if self.db_created else 0,
            "provisioning_mode": self.provisioning_mode or "developer",
            "description": self.description or "",
            "workers": int(self.workers or 0),
            "log_level": self.log_level or "info",
            "python_binary": self.python_binary or "",
            "addons_state": json.dumps(self.addons_state or []),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict | sqlite3.Row) -> "Instance":
        """Build an Instance from a SQLite row (dict or sqlite3.Row).

        Tolerant of pre-Sprint-2 databases missing the new columns
        (password_storage/last_error) — sensible defaults apply.
        """
        data = dict(row) if not isinstance(row, dict) else dict(row)
        get = data.get

        def _loads(value: object) -> list[str]:
            if not value:
                return []
            if isinstance(value, list):
                return list(value)
            try:
                parsed = json.loads(value)  # type: ignore[arg-type]
                return list(parsed) if isinstance(parsed, list) else []
            except (TypeError, ValueError):
                return []

        return cls(
            id=get("id"),
            name=get("name"),
            version=get("version") or "",
            mode=get("mode") or "managed",
            path=get("path") or "",
            venv_path=get("venv_path") or "",
            community_path=get("community_path") or "",
            enterprise_path=get("enterprise_path"),
            custom_addons_path=get("custom_addons_path") or "",
            conf_path=get("conf_path") or "",
            log_path=get("log_path") or "",
            port=int(get("port") or 8069),
            db_user=get("db_user") or "odoo",
            db_password=get("db_password") or "",
            password_storage=get("password_storage") or "plaintext",
            primary_db=get("primary_db") or "",
            tracked_dbs=_loads(get("tracked_dbs")),
            auto_update_modules=_loads(get("auto_update_modules")),
            pending_update_modules=_loads(get("pending_update_modules")),
            status=get("status") or "draft",
            pid=get("pid"),
            last_error=get("last_error"),
            db_created=bool(get("db_created")),
            provisioning_mode=get("provisioning_mode") or "developer",
            description=get("description") or "",
            workers=int(get("workers") or 0),
            log_level=get("log_level") or "info",
            python_binary=get("python_binary") or "",
            addons_state=_loads(get("addons_state")),
            created_at=get("created_at") or _utcnow_iso(),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Instance":
        data = dict(data)
        data.setdefault("id", str(uuid.uuid4()))
        data.setdefault("created_at", _utcnow_iso())
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
