"""Sprint BK.2 tests: headless runner behavior + timer unit management."""

import os
import subprocess
import sys
from pathlib import Path

from odoo_vite.core import backup_scheduler as bs


def _run_runner(*args, env_extra=None):
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "odoo_vite.backup_runner", *args],
        capture_output=True, text=True, env=env, timeout=60)


def test_unit_text_sane():
    service, timer = bs.timer_unit_text("/usr/bin/python3", "/app")
    assert "odoo_vite.backup_runner --check-due" in service
    assert "Type=oneshot" in service
    assert "OnCalendar=minutely" in timer
    assert "WantedBy=timers.target" in timer
    # WorkingDirectory must be the project root (parent of the odoo_vite
    # package), or `python -m` fails — caught live by E2E once.
    _, default_timer = bs.timer_unit_text()
    root = Path(bs.__file__).resolve().parents[2]
    assert f"WorkingDirectory={root}" in _
    assert (root / "odoo_vite" / "__init__.py").is_file()


def test_timer_status_without_systemctl(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "_unit_dir", lambda: tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # no systemctl here
    status = bs.timer_status()
    assert status == {"installed": False, "enabled": False,
                      "active": False, "detail": ""}


def test_runner_unknown_schedule(tmp_path):
    db = tmp_path / "bk.db"
    proc = _run_runner("--schedule-id", "nope",
                       env_extra={"ODOO_VITE_DB": str(db)})
    assert proc.returncode == 1
    assert "not found" in proc.stdout.lower() + proc.stderr.lower()


def test_runner_check_due_empty(tmp_path):
    db = tmp_path / "bk.db"
    proc = _run_runner("--check-due", env_extra={"ODOO_VITE_DB": str(db)})
    assert proc.returncode == 0, proc.stderr


def test_runner_headless_no_gtk(tmp_path):
    """The runner path must never import Gtk (no display available here)."""
    import json

    db = tmp_path / "bk.db"
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env["ODOO_VITE_DB"] = str(db)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys, json; import odoo_vite.backup_runner;"
         " print(json.dumps(sorted(sys.modules)))"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    mods = [m for m in json.loads(proc.stdout)
            if m == "gi" or m.startswith("gi.")]
    assert mods == [], f"runner pulled in GUI modules: {mods}"


def test_lock_contention_skips(tmp_path, monkeypatch):
    import fcntl
    db = tmp_path / "bk.db"
    lock = Path.home() / ".local" / "share" / "odoo-vite" / "backup-runner.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = _run_runner("--check-due", env_extra={"ODOO_VITE_DB": str(db)})
    assert proc.returncode == 0
    assert "skipping" in proc.stdout.lower()
