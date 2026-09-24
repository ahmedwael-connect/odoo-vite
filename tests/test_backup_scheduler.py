"""Sprint BK.1 tests: cron parsing/next-run, retention selection, schedule CRUD."""

from datetime import datetime

import pytest

from odoo_vite.core import backup_scheduler as bs


def test_parse_common_expressions():
    assert bs.parse_cron("0 2 * * *")["hour"] == {2}
    assert bs.parse_cron("*/15 * * * *")["minute"] == set(range(0, 60, 15))
    assert bs.parse_cron("0 0 1 * *")["dom"] == {1}
    assert bs.parse_cron("30 3 * * mon")["dow"] == {1}
    assert bs.parse_cron("0 0 * * 0")["dow"] == {0}
    assert bs.parse_cron("0 0 * * 7")["dow"] == {0}  # Sunday 7 -> 0


def test_parse_rejects_garbage():
    for bad in ["", "0 2 * *", "* * * * * *", "61 * * * *",
                "0 25 * * *", "abc * * * *", "*/0 * * * *",
                "0 2 * * * # comment"]:
        with pytest.raises(ValueError):
            bs.parse_cron(bad)


def test_next_run_known_times():
    # Daily 02:30 from Monday noon -> Tuesday 02:30.
    assert bs.next_run("30 2 * * *",
                       datetime(2026, 9, 21, 12, 0)) == datetime(2026, 9, 22, 2, 30)
    # Every 15 min: 12:00 -> 12:15 (strictly after).
    assert bs.next_run("*/15 * * * *",
                       datetime(2026, 9, 21, 12, 0)) == datetime(2026, 9, 21, 12, 15)
    # Mondays 09:00 from a Monday 10:00 -> next Monday.
    assert bs.next_run("0 9 * * mon",
                       datetime(2026, 9, 21, 10, 0)) == datetime(2026, 9, 28, 9, 0)
    # First of month.
    assert bs.next_run("0 0 1 * *",
                       datetime(2026, 9, 21, 12, 0)) == datetime(2026, 10, 1, 0, 0)


def test_describe_never_raises():
    assert "Next run" in bs.describe("0 2 * * *")
    assert "Invalid" in bs.describe("not a cron")


def _make_dumps(root, specs):
    # specs: [(name, age_days)]
    import time
    from pathlib import Path
    now = time.time()
    for name, age in specs:
        p = Path(root) / name
        p.write_bytes(b"x" * 10)
        old = now - age * 86400
        import os
        os.utime(p, (old, old))


def test_retention_keep_last_n(tmp_path):
    _make_dumps(tmp_path, [(f"b{i}.dump", i) for i in range(5)])
    victims = bs.retention_victims(tmp_path, retention_n=3, retention_days=0)
    assert sorted(v["path"] for v in victims) == sorted(
        str(tmp_path / f"b{i}.dump") for i in (3, 4))


def test_retention_keep_days(tmp_path):
    _make_dumps(tmp_path, [("new.dump", 1), ("old.dump", 30)])
    victims = bs.retention_victims(tmp_path, retention_n=0, retention_days=7)
    assert [v["path"] for v in victims] == [str(tmp_path / "old.dump")]


def test_retention_off_keeps_all(tmp_path):
    _make_dumps(tmp_path, [("a.dump", 100)])
    assert bs.retention_victims(tmp_path, 0, 0) == []


def test_schedule_crud(tmp_path):
    db = tmp_path / "bk.db"
    assert bs.list_schedules(db_path=db) == []
    res = bs.create_schedule("inst-1", ["db1"], "0 2 * * *", db_path=db)
    assert res.ok, res.message
    sid = res.data["id"]
    scheds = bs.list_schedules("inst-1", db_path=db)
    assert len(scheds) == 1 and scheds[0].cron == "0 2 * * *"
    assert scheds[0].databases == ["db1"] and scheds[0].enabled
    assert bs.set_enabled(sid, False, db_path=db).ok
    assert bs.get_schedule(sid, db_path=db).enabled is False
    assert bs.delete_schedule(sid, db_path=db).ok
    assert bs.list_schedules("inst-1", db_path=db) == []


def test_create_rejects_bad_input(tmp_path):
    db = tmp_path / "bk.db"
    assert not bs.create_schedule("i", ["d"], "nonsense", db_path=db).ok
    assert not bs.create_schedule("i", [], "0 2 * * *", db_path=db).ok
    assert not bs.create_schedule("i", ["d"], "0 2 * * *", retention_n=-1,
                                  db_path=db).ok


def test_due_schedules_minute_match(tmp_path):
    db = tmp_path / "bk.db"
    res = bs.create_schedule("inst-9", ["dbx"], "* * * * *", db_path=db)
    assert res.ok
    due = bs.due_schedules(datetime.now(), db_path=db)
    assert [s.id for s in due] == [res.data["id"]]
    # Disabled schedules are never due.
    bs.set_enabled(res.data["id"], False, db_path=db)
    assert bs.due_schedules(datetime.now(), db_path=db) == []
