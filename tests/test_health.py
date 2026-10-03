"""3.1.0 C2: instance health monitor — cheap probes, never raise."""

import os
import time

from odoo_vite.core import health
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance


def _inst(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "h.db"))
    fields = dict(name="H1", version="17.0", path=str(tmp_path),
                  log_path=str(tmp_path / "odoo.log"))
    fields.update(over)
    inst = Instance(**fields)
    assert create_instance(inst).ok
    return inst


def test_check_instance_report_shape(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    rep = health.check_instance(inst.id)
    d = rep.as_dict()
    assert d["instance_id"] == inst.id
    assert [c["name"] for c in d["checks"]] == [
        "process", "venv", "postgres", "disk", "log"]
    assert d["level"] in {"ok", "warn", "error", "unknown"}
    assert d["summary"]
    known = {"ok", "warn", "error", "unknown", "info"}
    assert all(c["state"] in known for c in d["checks"])
    assert all(isinstance(c["detail"], str) for c in d["checks"])


def test_missing_instance_is_error():
    rep = health.check_instance("no-such-id")
    assert rep.level == "error"
    assert "not found" in rep.summary
    assert rep.checks[0].state == "error"


def test_process_and_venv_checks(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)  # pid=None → stopped, venv unset
    assert health._check_process(None).state == "info"
    assert health._check_process(4242).state == "ok"
    venv = health._check_venv(inst)
    assert venv.state == "warn" and "rebuild" in venv.detail


def test_log_check_levels(tmp_path, monkeypatch):
    log = tmp_path / "odoo.log"
    inst = _inst(tmp_path, monkeypatch)
    assert health._check_log(inst, running=True).state == "info"  # no file

    log.write_text("\n".join(f"2024 INFO line {i}" for i in range(5)) + "\n")
    c = health._check_log(inst, running=True)
    assert c.state == "ok"

    log.write_text("\n".join(f"2024 ERROR boom {i}" for i in range(12)) + "\n")
    c = health._check_log(inst, running=True)
    assert c.state == "warn" and "error lines" in c.detail

    log.write_text("2024 INFO quiet\n")
    old = time.time() - 10_000
    os.utime(log, (old, old))
    c = health._check_log(inst, running=True)
    assert c.state == "warn" and "no writes" in c.detail
    assert health._check_log(inst, running=False).state == "info"


def test_disk_check_reports_free_space(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch, path=str(tmp_path))
    c = health._check_disk(inst)
    assert c.state == "ok" and "free" in c.detail


def test_safe_wraps_probe_exceptions():
    def boom():
        raise RuntimeError("kaput")

    c = health._safe("probe", boom)
    assert c.state == "unknown"
    assert "kaput" in c.detail


def test_summary_counts_levels():
    ok = health.Check("a", "ok", "")
    warn = health.Check("b", "warn", "")
    err = health.Check("c", "error", "")
    assert "healthy" in health._summary("ok", [ok])
    assert "1 warning" in health._summary("warn", [ok, warn])
    assert "1 error" in health._summary("error", [ok, warn, err])
