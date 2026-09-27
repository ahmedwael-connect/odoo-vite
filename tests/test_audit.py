"""Ticket 3.8 test: audit log roundtrip + Sprint 3 schema migration."""


from odoo_vite.core import audit as audit_log
from odoo_vite.core.registry import ensure_schema, get_instance


def test_audit_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    audit_log.log_event("id-1", "Demo", "start", "pid=1")
    audit_log.log_event("id-1", "Demo", "stop", "graceful")
    events = audit_log.read_events()
    assert [e["action"] for e in events] == ["start", "stop"]
    assert events[0]["instance_name"] == "Demo" and "ts" in events[0]
    # file is JSON-lines
    assert len((tmp_path / "audit.log").read_text().splitlines()) == 2


def test_audit_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", "/nonexistent-dir-xyz/audit.log")
    audit_log.log_event("x", "y", "start")  # must not raise
    assert audit_log.read_events() == []


def test_audit_rotates_when_over_limit(tmp_path, monkeypatch):
    """U4.1: exceeding MAX_BYTES shifts audit.log -> .1, keeps logging."""
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    monkeypatch.setattr(audit_log, "MAX_BYTES", 200)
    for i in range(20):
        audit_log.log_event("id-1", "Demo", "start", f"event-{i}")
    rotated = tmp_path / "audit.log.1"
    assert rotated.exists(), "expected a rotated audit.log.1"
    # New events still land in the live file and stay readable.
    audit_log.log_event("id-1", "Demo", "stop", "after-rotation")
    events = audit_log.read_events()
    assert events and events[-1]["action"] == "stop"


def test_db_created_migration(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE instances (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL)")
    conn.execute("INSERT INTO instances (id, name) VALUES ('a','b')")
    conn.commit()
    conn.close()
    res = ensure_schema(db)
    assert res.ok
    assert "db_created" in res.data["columns"]
    inst = get_instance("a", db)
    assert inst is not None and inst.db_created is False
    assert inst.status == "draft"  # Sprint 2 default preserved
