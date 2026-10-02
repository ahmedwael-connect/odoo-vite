"""PSS-6a: log ops with REAL backends (no PG).

Framework-free by construction (imports core + ops.logs only): per
docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. File-backed ops (search/doctor) run against tmp logs;
PG-backed slow_refresh exercises the failure path without a server.
"""

import asyncio



from odoo_vite.ops.logs import (  # noqa: E402
    LOG_LEVELS,
    LogOps,
    format_doctor_lines,
    format_search_lines,
    format_slow_lines,
    open_svg_external,
    parse_profile_duration,
)


def _ops():
    messages = []
    sinks = {"search": [], "doctor": [], "slow": [], "profile": []}
    ops = LogOps(lambda m, k="info": messages.append((m, k)),
                 lambda i, o, m, r: sinks["search"].append((i, o, m, r)),
                 lambda i, f: sinks["doctor"].append((i, f)),
                 lambda i, o, m, r: sinks["slow"].append((i, o, m, r)),
                 lambda i, o, m, s: sinks["profile"].append((i, o, m, s)))
    return ops, messages, sinks


def test_format_search_lines():
    matches = [{"lineno": 7, "line": "ERROR boom", "before": ["a"],
                "after": ["b", "c"]}]
    lines = format_search_lines(matches)
    assert lines[0] == "line 7: ERROR boom"
    assert lines[1:] == ["    a", "    b", "    c"]
    assert format_search_lines([]) == []
    big = [{"lineno": i, "line": "x" * 500} for i in range(300)]
    assert len(format_search_lines(big)) == 200  # capped
    assert len(format_search_lines(big)[0]) <= 220  # truncated


def test_format_doctor_lines():
    findings = [{"severity": "high", "title": "Port in use", "count": 3},
                {"severity": "low", "title": "Slow start", "count": 1}]
    assert format_doctor_lines(findings) == [
        "[!] Port in use  (×3)", "[i] Slow start"]
    assert format_doctor_lines([]) == []


def test_format_slow_lines():
    rows = [{"query": "SELECT 1", "calls": 5, "total_ms": 12.5}]
    assert format_slow_lines(rows) == ["SELECT 1 — 5 calls · total 12.5 ms"]
    assert len(format_slow_lines([{}] * 30)) == 20  # capped


def test_parse_profile_duration():
    assert parse_profile_duration("10s") == 10
    assert parse_profile_duration("5s") == 5
    assert parse_profile_duration("") == 10
    assert parse_profile_duration("bogus") == 10
    assert LOG_LEVELS[0] == "All levels"


def test_unknown_ids_fail():
    ops, messages, sinks = _ops()
    res = asyncio.run(ops.search("no-such-id", "x", None))
    assert not res.ok
    res = asyncio.run(ops.doctor("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.slow_refresh("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.profile("no-such-id"))
    assert not res.ok
    assert all(v == [] for v in sinks.values())
    assert any("disappeared" in m or "recorded" in m
               for m, _k in messages)


def _db_instance(tmp_path, monkeypatch, name="LG", **overrides):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "logs.db"))
    fields = dict(name=name, version="17.0", path=str(tmp_path),
                  log_path=str(tmp_path / "odoo.log"))
    fields.update(overrides)
    inst = Instance(**fields)
    assert create_instance(inst).ok
    return inst


def test_search_real_file(tmp_path, monkeypatch):
    log = tmp_path / "odoo.log"
    log.write_text("2024 INFO start\n2024 ERROR boom\n2024 INFO end\n")
    inst = _db_instance(tmp_path, monkeypatch)
    ops, _, sinks = _ops()
    res = asyncio.run(ops.search(inst.id, "boom", None))
    assert res.ok, res.message
    iid, ok, _msg, matches = sinks["search"][0]
    assert (iid, ok) == (inst.id, True)
    assert len(matches) == 1 and matches[0]["line"].endswith("boom")
    res = asyncio.run(ops.search(inst.id, "   ", None))
    assert not res.ok and "regex" in res.message


def test_doctor_empty_log(tmp_path, monkeypatch):
    (tmp_path / "odoo.log").write_text("INFO all quiet\n")
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, sinks = _ops()
    res = asyncio.run(ops.doctor(inst.id))
    assert res.ok
    assert sinks["doctor"] == [(inst.id, [])]
    assert any("no known issues" in m for m, _k in messages)


def test_slow_refresh_without_server(tmp_path, monkeypatch):
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, sinks = _ops()
    res = asyncio.run(ops.slow_refresh(inst.id))
    assert not res.ok  # no PG here: extension check or query fails
    iid, ok, _msg, rows = sinks["slow"][0]
    assert (iid, ok, rows) == (inst.id, False, [])
    assert any(m for m, _k in messages)


def test_profile_guards(tmp_path, monkeypatch):
    inst = _db_instance(tmp_path, monkeypatch)
    ops, messages, sinks = _ops()
    res = asyncio.run(ops.profile(inst.id, 5))
    assert not res.ok and "running" in res.message  # stopped instance
    assert sinks["profile"] == []
    assert any("running" in m for m, _k in messages)


def test_open_svg_external_reports_saved():
    import queue as queue_mod

    q: queue_mod.Queue = queue_mod.Queue()
    asyncio.run(open_svg_external(q, "/no/such/graph.svg"))
    kind, payload = q.get_nowait()
    assert kind == "message"
    assert payload[0].startswith("Profile saved: ")
