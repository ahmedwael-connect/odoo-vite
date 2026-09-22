"""Ticket 1.2 test: SQLite registry CRUD. No GTK involved."""

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import (
    create_instance,
    delete_instance,
    get_instance,
    init_db,
    list_instances,
    update_instance,
)


def _dummy(name="demo-17", version="17.0") -> Instance:
    return Instance(
        name=name,
        version=version,
        mode="managed",
        path=f"/tmp/odoo-vite/{name}",
        venv_path=f"/tmp/odoo-vite/{name}/venv",
        community_path=f"/tmp/odoo-vite/{name}/odoo",
        custom_addons_path=f"/tmp/odoo-vite/{name}/custom_addons",
        conf_path=f"/tmp/odoo-vite/{name}/odoo.conf",
        log_path=f"/tmp/odoo-vite/{name}/odoo.log",
        port=8069,
        db_user="odoo",
        db_password="odoo",
        primary_db="demo",
        tracked_dbs=["demo"],
    )


def test_crud_roundtrip(tmp_path):
    db = tmp_path / "test.db"
    assert init_db(db).ok

    inst = _dummy()
    res = create_instance(inst, db)
    assert res.ok, res.message

    rows = list_instances(db)
    assert len(rows) == 1
    assert rows[0].name == "demo-17"
    assert rows[0].tracked_dbs == ["demo"]

    fetched = get_instance(inst.id, db)
    assert fetched is not None
    assert fetched.version == "17.0"

    upd = update_instance(inst.id, db, port=8070, status="running")
    assert upd.ok, upd.message
    assert get_instance(inst.id, db).port == 8070

    # duplicate name must fail cleanly (Result, not exception)
    dup = create_instance(_dummy(), db)
    assert not dup.ok

    dele = delete_instance(inst.id, db)
    assert dele.ok, dele.message
    assert list_instances(db) == []
    assert get_instance(inst.id, db) is None


def test_update_delete_missing_row(tmp_path):
    db = tmp_path / "test.db"
    assert init_db(db).ok
    assert not update_instance("no-such-id", db, port=1).ok
    assert not delete_instance("no-such-id", db).ok
