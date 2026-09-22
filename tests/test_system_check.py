"""Ticket 1.3 test: system requirements checker returns a structured report."""

from odoo_vite.core.system_check import (
    check_requirements,
    get_os_info,
    install_requirements,
)


def test_os_detection():
    info = get_os_info()
    assert "pretty" in info


def test_check_requirements_structure():
    res = check_requirements("17.0")
    assert res.ok, res.message
    report = res.data
    for key in ("missing", "present", "python_version_ok", "os", "details"):
        assert key in report, f"report lacks '{key}'"
    assert isinstance(report["missing"], list)
    assert isinstance(report["present"], list)
    # python3 binary must be present in any dev/CI environment
    assert "python3" in report["present"]


def test_install_dry_run_builds_pkexec_command():
    res = install_requirements(["git", "libpq-dev"], dry_run=True)
    assert res.ok, res.message
    cmd = res.data["command"]
    assert cmd[:3] == ["pkexec", "apt-get", "install"]
    assert "git" in cmd and "libpq-dev" in cmd
    assert "sudo" not in cmd[:2]  # pkexec, never raw sudo


def test_install_empty_is_noop():
    assert install_requirements([], dry_run=True).ok
