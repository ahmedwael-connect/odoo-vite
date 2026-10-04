"""PSS-5b: configuration ops with REAL backends (no PG).

Framework-free by construction (imports core + ops.configuration only):
per docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. asyncio.run() drives the coroutines on the main thread.
"""

import asyncio
from types import SimpleNamespace



from odoo_vite.ops.configuration import (  # noqa: E402
    COMMON_KEYS,
    LOG_LEVELS,
    ConfigOps,
    pick_open_dir,
    read_conf_view,
    validate_meta,
)


def _ops():
    messages, refreshes = [], []
    ops = ConfigOps(lambda m, k="info": messages.append((m, k)),
                    lambda: refreshes.append(1))
    return ops, messages, refreshes


def _conf_text():
    return ("[options]\n"
            "db_host = localhost\n"
            "db_user = odoo\n"
            "addons_path = /a,/b\n"
            "\n"
            "[queue_job]\n"
            "channels = root:1\n")


def _inst(conf_path="", **overrides):
    base = dict(conf_path=conf_path, description="", workers=0,
                log_level="info", python_binary="",
                auto_update_modules=[], pending_update_modules=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def test_read_conf_view(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_text(_conf_text())
    view = read_conf_view(_inst(str(conf), description="D", workers=2,
                                auto_update_modules=["sale"],
                                pending_update_modules=["stock"]))
    assert view["error"] == ""
    assert view["conf_path"] == str(conf)
    assert view["lines"][0] == "db_host = localhost"
    assert view["common"]["db_host"] == "localhost"
    assert view["common"]["logfile"] == ""  # absent key, not an error
    assert view["addons_path"] == "/a,/b"
    assert view["backup_path"] == ""
    assert view["description"] == "D"
    assert view["workers"] == 2
    # 3.2.0 F1: both update lists feed the metadata-card editor.
    assert view["auto_update_modules"] == ["sale"]
    assert view["pending_update_modules"] == ["stock"]


def test_read_conf_view_errors():
    assert read_conf_view(_inst())["error"] == "No conf recorded."
    assert read_conf_view(None)["error"] == "No conf recorded."
    view = read_conf_view(_inst("/no/such/conf"))
    assert "Cannot read conf" in view["error"]


def test_validate_meta():
    import sys

    assert validate_meta({"python_binary": ""}) is None
    assert validate_meta({"python_binary": "   "}) is None
    assert validate_meta({"python_binary": sys.executable}) is None
    err = validate_meta({"python_binary": "/no/such/python"})
    assert err is not None and "/no/such/python" in err


def test_pick_open_dir_empty_without_zenity(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _c: None)
    assert pick_open_dir("T") == ""


def test_unknown_ids_fail():
    ops, messages, _, = _ops()
    res = asyncio.run(ops.save("no-such-id", {"db_host": "x"}))
    assert not res.ok
    res = asyncio.run(ops.restore("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.regenerate("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.meta_save("no-such-id", {}))
    assert not res.ok
    res = asyncio.run(ops.apply_addons("no-such-id", []))
    assert not res.ok
    assert any("disappeared" in m for m, _k in messages)


def _db_instance(tmp_path, monkeypatch, name="CF", **overrides):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "conf.db"))
    fields = dict(name=name, version="17.0", path=str(tmp_path),
                  conf_path=str(tmp_path / "odoo.conf"))
    fields.update(overrides)
    inst = Instance(**fields)
    assert create_instance(inst).ok
    return inst


def test_save_backup_restore_round_trip(tmp_path, monkeypatch):
    (tmp_path / "odoo.conf").write_text(_conf_text())
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.save(inst.id, {"db_host": "db.internal"}))
    assert res.ok, res.message
    assert "backup kept" in res.message
    assert (tmp_path / "odoo.conf.bak").is_file()
    view = read_conf_view(_inst(str(tmp_path / "odoo.conf")))
    assert view["common"]["db_host"] == "db.internal"
    assert view["backup_path"] != ""
    # Unknown sections survive the write.
    assert "[queue_job]" in (tmp_path / "odoo.conf").read_text()
    res = asyncio.run(ops.restore(inst.id))
    assert res.ok, res.message
    view = read_conf_view(_inst(str(tmp_path / "odoo.conf")))
    assert view["common"]["db_host"] == "localhost"
    assert messages and refreshes


def test_save_refuses_bad_values(tmp_path, monkeypatch):
    (tmp_path / "odoo.conf").write_text(_conf_text())
    inst = _db_instance(tmp_path, monkeypatch)
    ops, _, _ = _ops()
    res = asyncio.run(ops.save(inst.id, {"db_host": ""}))
    assert not res.ok and "empty" in res.message
    res = asyncio.run(ops.save(inst.id, {"db_port": "notaport"}))
    assert not res.ok


def test_meta_save_round_trip(tmp_path, monkeypatch):
    (tmp_path / "odoo.conf").write_text(_conf_text())
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, _ = _ops()
    res = asyncio.run(ops.meta_save(inst.id, {
        "description": "Team A", "workers": 0, "log_level": "debug",
        "python_binary": ""}))
    assert res.ok, res.message
    from odoo_vite.core.registry import get_instance

    assert get_instance(inst.id).description == "Team A"
    text = (tmp_path / "odoo.conf").read_text()
    assert "log_level = debug" in text
    res = asyncio.run(ops.meta_save(inst.id, {"workers": "xx"}))
    assert not res.ok and "Invalid workers" in res.message
    assert any("Metadata saved" in m for m, _k in messages)


def test_meta_save_partial_payload_and_queue(tmp_path, monkeypatch):
    """3.2.0 F1: absent keys are untouched, so a queue-only write from the
    Overview (or Modules view) never wipes description/workers/log_level."""
    (tmp_path / "odoo.conf").write_text(_conf_text())
    inst = _db_instance(tmp_path, monkeypatch, description="Keep me")
    ops, _, _ = _ops()
    from odoo_vite.core.registry import get_instance

    res = asyncio.run(ops.meta_save(
        inst.id, {"pending_update_modules": ["stock", " sale ", ""]}))
    assert res.ok, res.message
    row = get_instance(inst.id)
    assert row.pending_update_modules == ["stock", "sale"]  # stripped, no blanks
    assert row.description == "Keep me"

    res = asyncio.run(ops.meta_save(inst.id, {"description": "New"}))
    assert res.ok, res.message
    row = get_instance(inst.id)
    assert row.description == "New"
    assert row.pending_update_modules == ["stock", "sale"]  # still untouched

    res = asyncio.run(ops.meta_save(
        inst.id, {"pending_update_modules": "stock"}))
    assert not res.ok and "list" in res.message

    res = asyncio.run(ops.meta_save(inst.id, {"pending_update_modules": []}))
    assert res.ok, res.message
    assert get_instance(inst.id).pending_update_modules == []


def test_apply_addons_round_trip(tmp_path, monkeypatch):
    (tmp_path / "odoo.conf").write_text(_conf_text())
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, _ = _ops()
    entries = [{"path": "/b", "enabled": True},
               {"path": "/a", "enabled": True},
               {"path": "/c", "enabled": False}]
    res = asyncio.run(ops.apply_addons(inst.id, entries))
    assert res.ok, res.message
    view = read_conf_view(_inst(str(tmp_path / "odoo.conf")))
    # Order preserved, disabled excluded (derived string rule).
    assert view["addons_path"] == "/b,/a"
    from odoo_vite.core.registry import get_instance

    stored = get_instance(inst.id).addons_state
    assert [e["path"] for e in stored] == ["/b", "/a", "/c"]
    res = asyncio.run(ops.apply_addons(inst.id, []))
    assert not res.ok and "empty addons_path" in res.message
    assert messages


def test_constants_match_qt():
    """Key lists, frozen at cutover (were parity-tested against the Qt
    view until ui_qt/ was deleted in PSS-9)."""
    assert COMMON_KEYS == ["db_host", "db_port", "db_user", "xmlrpc_port",
                           "logfile"]
    assert LOG_LEVELS == ["info", "debug", "debug_sql", "warning", "error",
                          "critical"]
