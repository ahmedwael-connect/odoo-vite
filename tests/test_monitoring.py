"""Sprint 8 Part B unit tests: tail, search, doctor, stat, profiler."""

import pytest

from odoo_vite.core import log_doctor, log_search, log_tail


@pytest.fixture
def logfile(tmp_path):
    path = tmp_path / "odoo.log"
    lines = [
        "2026-09-22 09:11:44,120 100 INFO server odoo.service.server: start",
        "2026-09-22 09:11:45,001 100 WARNING db odoo.addons.base: slow-ish",
        "2026-09-22 09:11:46,002 100 ERROR db odoo.sql_db: bad query",
        "Traceback (most recent call last):",
        '  File "/x/odoo/modules/loading.py", line 1, in load',
        "ModuleNotFoundError: No module named 'pkg_resources'",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


# ------------------------------------------------------------------ tail
def test_follower_polls_incrementally(logfile):
    follower = log_tail.LogFollower(str(logfile))
    first = follower.poll()
    assert len(first["lines"]) == 6 and not first["rotated"]
    assert follower.poll()["lines"] == []
    with open(logfile, "a") as fh:
        fh.write("2026-09-22 09:12:00,000 100 INFO server tick\n")
    second = follower.poll()
    assert second["lines"] == ["2026-09-22 09:12:00,000 100 INFO server tick"]
    # partial line held back, not emitted half-formed
    with open(logfile, "ab") as fh:
        fh.write(b"half-")
    assert follower.poll()["lines"] == []
    with open(logfile, "ab") as fh:
        fh.write(b"line\n")
    assert follower.poll()["lines"] == ["half-line"]


def test_follower_detects_truncation(logfile):
    follower = log_tail.LogFollower(str(logfile))
    assert follower.poll()["lines"]
    logfile.write_text("fresh start\n")
    res = follower.poll()
    assert res["rotated"] is True
    assert res["lines"] == ["fresh start"]


def test_follower_missing_file(tmp_path):
    res = log_tail.LogFollower(str(tmp_path / "nope.log")).poll()
    assert res["missing"] is True and res["lines"] == []


def test_read_last_n(tmp_path):
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"line {i}" for i in range(5000)) + "\n")
    assert log_tail.read_last_n(str(path), 10) == [f"line {i}" for i in range(4990, 5000)]
    assert log_tail.read_last_n(str(tmp_path / "nope.log")) == []


def test_no_double_emit_after_initial_fill(tmp_path):
    """Initial read_last_n + follower must not duplicate lines (RC-found)."""
    path = tmp_path / "a.log"
    path.write_text("\n".join(f"line {i}" for i in range(50)) + "\n")
    initial = log_tail.read_last_n(str(path), 100)
    assert len(initial) == 50
    follower = log_tail.LogFollower(str(path))
    follower.sync_to_end()
    assert follower.poll()["lines"] == []
    with open(path, "a") as fh:
        fh.write("line 50\n")
    assert follower.poll()["lines"] == ["line 50"]


# ------------------------------------------------------------------ search
def test_search_regex_level_time_context(logfile):
    res = log_search.search_file(str(logfile), "bad query")
    assert res.ok and len(res.data["matches"]) == 1
    assert res.data["matches"][0]["lineno"] == 3

    res = log_search.search_file(str(logfile), "odoo", level="ERROR")
    assert res.ok and len(res.data["matches"]) == 1

    res = log_search.search_file(str(logfile), "odoo", level="INFO")
    assert res.ok and len(res.data["matches"]) == 1

    from datetime import datetime

    res = log_search.search_file(
        str(logfile), "odoo",
        since=datetime(2026, 9, 22, 9, 11, 45),
        until=datetime(2026, 9, 22, 9, 11, 46), context=1)
    assert res.ok and len(res.data["matches"]) == 1
    match = res.data["matches"][0]
    assert match["lineno"] == 2
    assert match["before"] == ["2026-09-22 09:11:44,120 100 INFO server odoo.service.server: start"]
    assert match["after"] == ["2026-09-22 09:11:46,002 100 ERROR db odoo.sql_db: bad query"]

    res = log_search.search_file(str(logfile), "(unclosed")
    assert not res.ok and "Invalid regex" in res.message


def test_search_streams_chunks(tmp_path, monkeypatch):
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"line {i} needle" if i == 2500 else f"line {i}"
                              for i in range(5000)) + "\n")
    res = log_search.search_file(str(path), "needle", chunk=1024)
    assert res.ok and len(res.data["matches"]) == 1
    assert res.data["matches"][0]["lineno"] == 2501
    assert res.data["scanned_lines"] == 5000


# ------------------------------------------------------------------ doctor
def test_doctor_signatures(logfile):
    findings = log_doctor.diagnose_file(str(logfile))
    by_id = {f["sig_id"]: f for f in findings}
    # pkg_resources dogfooded from our own history
    assert by_id["pkg_resources"]["severity"] == "high"
    assert "Repair venv" in by_id["pkg_resources"]["suggestion"]
    # generic traceback catch-all also fires (never silent)
    assert "traceback" in by_id
    assert all("suggestion" in f and f["suggestion"] for f in findings)


def test_doctor_port_and_permission(tmp_path):
    path = tmp_path / "p.log"
    path.write_text(
        "OSError: [Errno 98] Address already in use\n"
        "PermissionError: [Errno 13] Permission denied: '/x/odoo.log'\n"
        "psycopg2.OperationalError: connection to server failed: Connection refused\n")
    findings = log_doctor.diagnose_file(str(path))
    by_id = {f["sig_id"]: f for f in findings}
    assert by_id["port_in_use"]["severity"] == "high"
    assert "ss -ltnp" in by_id["port_in_use"]["suggestion"]
    assert by_id["permission_denied"]["severity"] == "medium"
    assert by_id["db_connection"]["severity"] == "high"
    assert "traceback" not in by_id  # no traceback here: no generic noise


def test_doctor_dedupes_floods(tmp_path):
    path = tmp_path / "flood.log"
    path.write_text("OSError: [Errno 98] Address already in use\n" * 30)
    findings = log_doctor.diagnose_file(str(path))
    assert len(findings) == 1 and findings[0]["count"] == 30


# ------------------------------------------------------------------ stat/profiler
def test_stat_enabled_probe(monkeypatch):
    from odoo_vite.core import db_manager

    monkeypatch.setattr(db_manager, "_run", lambda *a, **k: (0, "1"))
    assert db_manager.pg_stat_statements_enabled() is True
    monkeypatch.setattr(db_manager, "_run", lambda *a, **k: (0, "0"))
    assert db_manager.pg_stat_statements_enabled() is False
    monkeypatch.setattr(db_manager, "_run", lambda *a, **k: (1, "boom"))
    assert db_manager.pg_stat_statements_enabled() is False


def test_slow_queries_parsing(monkeypatch):
    from odoo_vite.core import db_manager

    def _fake(cmd, env_extra=None, timeout=60):
        joined = " ".join(cmd)
        if "pg_extension" in joined:
            return 0, "1"
        assert "pg_stat_statements" in joined
        return 0, "SELECT 1\x1f42\x1f100.5\x1f2.38"

    monkeypatch.setattr(db_manager, "_run", _fake)
    res = db_manager.slow_queries("mydb")
    assert res.ok, res.message
    assert res.data["queries"] == [{"query": "SELECT 1", "calls": 42,
                                    "total_ms": 100.5, "mean_ms": 2.38}]

    monkeypatch.setattr(db_manager, "_run", lambda *a, **k: (0, "0"))
    res = db_manager.slow_queries("mydb")
    assert not res.ok and "CREATE EXTENSION" in res.message


def test_profiler_cmd_and_validation(tmp_path, monkeypatch):
    import odoo_vite.core.proc as proc
    from odoo_vite.core import profiler

    if profiler.py_spy_path() is None:
        pytest.skip("py-spy not installed")
    assert profiler.py_spy_path() is not None  # installed in this env
    assert profiler.profile_pid("nope").ok is False

    calls = []

    # build a fake success Result without importing Result twice
    from odoo_vite.core.result import Result

    def _fake2(cmd, progress_cb=None, cancel=None, timeout=0,
               cwd=None, env=None, stdin_text=None):
        calls.append(cmd)
        from pathlib import Path as _P

        for token in cmd:
            if str(token).endswith(".svg"):
                _P(token).write_bytes(b"<svg>ok</svg>")
        return Result.success(data={"lines": [], "returncode": 0,
                                    "cancelled": False})

    monkeypatch.setattr(proc, "run_streaming", _fake2)
    monkeypatch.setattr(profiler.psutil, "pid_exists", lambda pid: True)
    dest = tmp_path / "out.svg"
    res = profiler.profile_pid(12345, duration=5, output_svg=dest)
    assert res.ok, res.message
    assert any("--pid" in cmd and "12345" in cmd for cmd in calls)
    assert any("-d" in cmd and "5" in cmd for cmd in calls)
    # duration clamped, not passed through raw
    res = profiler.profile_pid(12345, duration=9999, output_svg=tmp_path / "b.svg")
    assert res.data["duration"] == 120
