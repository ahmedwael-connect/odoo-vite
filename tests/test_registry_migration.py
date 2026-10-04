"""Ticket 2.1 test: ensure_schema() migrates a Sprint 1 database + keyring helpers."""

import sqlite3
import threading

from odoo_vite.core import provisioning
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import (
    create_instance,
    ensure_schema,
    get_db_password,
    get_instance,
    init_db,
    store_db_password,
)

OLD_SCHEMA = """
CREATE TABLE instances (
    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, version TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'managed', path TEXT NOT NULL DEFAULT '',
    venv_path TEXT NOT NULL DEFAULT '', community_path TEXT NOT NULL DEFAULT '',
    enterprise_path TEXT, custom_addons_path TEXT NOT NULL DEFAULT '',
    conf_path TEXT NOT NULL DEFAULT '', log_path TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 8069, db_user TEXT NOT NULL DEFAULT 'odoo',
    db_password TEXT NOT NULL DEFAULT '', primary_db TEXT NOT NULL DEFAULT '',
    tracked_dbs TEXT NOT NULL DEFAULT '[]', auto_update_modules TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'stopped', pid INTEGER,
    created_at TEXT NOT NULL DEFAULT ''
);
"""


def _old_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO instances (id, name, version, status) VALUES (?,?,?,?)",
        ("abc-1", "legacy", "16.0", "stopped"),
    )
    conn.commit()
    conn.close()


def test_migrate_sprint1_db(tmp_path):
    db = tmp_path / "legacy.db"
    _old_db(db)
    res = ensure_schema(db)
    assert res.ok, res.message
    assert "password_storage" in res.data["columns"]
    assert "last_error" in res.data["columns"]
    # 3.2.0 F1: legacy DBs gain the one-shot update queue column.
    assert "pending_update_modules" in res.data["columns"]

    inst = get_instance("abc-1", db)
    assert inst is not None
    assert inst.name == "legacy"
    assert inst.status == "stopped"  # existing values preserved
    assert inst.password_storage == "plaintext"  # default applied
    assert inst.last_error is None
    assert inst.pending_update_modules == []


def test_ensure_schema_idempotent_and_fresh(tmp_path):
    db = tmp_path / "fresh.db"
    assert init_db(db).ok
    first = ensure_schema(db)
    second = ensure_schema(db)
    assert first.ok and second.ok
    # new rows default to draft (Sprint 2 lifecycle)
    inst = Instance(name="n", path="/tmp/x")
    assert inst.status == "draft"
    assert create_instance(inst, db).ok
    assert get_instance(inst.id, db).status == "draft"


def test_concurrent_connects_apply_pending_migration_once(tmp_path,
                                                          monkeypatch):
    """3.1.0 B7: threads racing a pending ALTER must not die with
    'duplicate column name' — the re-check runs under BEGIN IMMEDIATE."""
    from odoo_vite.core import registry as reg

    db = tmp_path / "race.db"
    assert init_db(db).ok
    monkeypatch.setattr(
        reg, "_MIGRATIONS",
        list(reg._MIGRATIONS) + [
            ("race_col",
             "ALTER TABLE instances ADD COLUMN race_col TEXT DEFAULT ''"),
        ],
    )
    errors: list[Exception] = []

    def worker() -> None:
        try:
            reg._connect(db).close()
        except Exception as exc:  # noqa: BLE001 — collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []

    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(instances)")}
    conn.close()
    assert "race_col" in cols


def test_password_roundtrip():
    storage, column = store_db_password("test-id-123", "hunter2")
    assert storage in ("keyring", "plaintext")
    inst = Instance(name="pw", password_storage=storage, db_password=column)
    inst.id = "test-id-123"
    assert get_db_password(inst) == "hunter2"


def test_form_helpers(tmp_path):
    assert provisioning.slugify_db_name("Odoo Client A") == "odoo_client_a"
    assert provisioning.slugify_db_name("9lives!") == "odoo_9lives"
    assert provisioning.slugify_db_name("") == "odoo"
    port = provisioning.suggest_port(8069, tmp_path / "p.db")
    assert 1024 <= port <= 65535
    assert provisioning.is_port_free(port)
    p1 = provisioning.unique_instance_path("Does Not Exist 12345")
    assert not p1.exists()
    (provisioning.default_instance_path("x")).parent.mkdir(parents=True, exist_ok=True)
