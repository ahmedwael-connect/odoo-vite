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


def test_purge_missing_paths(tmp_path):
    """3.3.0 P2: purge registry rows whose folders are gone — never running
    rows, never files, idempotent, and reported per row."""
    from odoo_vite.core.registry import missing_path_instances, purge_missing_paths

    db = tmp_path / "test.db"
    assert init_db(db).ok

    gone = tmp_path / "gone"          # does not exist
    live_dir = tmp_path / "here"
    live_dir.mkdir()

    stale = Instance(name="Stale", path=str(gone), status="stopped")
    here = Instance(name="Here", path=str(live_dir), status="stopped")
    # running rows are skipped even when their tree vanished
    running = Instance(name="Live", path=str(tmp_path / "also-gone"),
                       status="running")
    assert create_instance(stale, db).ok
    assert create_instance(here, db).ok
    assert create_instance(running, db).ok

    assert {i.id for i in missing_path_instances(db)} == {stale.id}

    res = purge_missing_paths(db)
    assert res.ok, res.message
    assert [p["name"] for p in res.data["purged"]] == ["Stale"]
    assert {i.name for i in list_instances(db)} == {"Here", "Live"}
    assert get_instance(stale.id, db) is None

    # idempotent second run
    res = purge_missing_paths(db)
    assert res.ok and res.data["purged"] == []
    assert len(list_instances(db)) == 2
