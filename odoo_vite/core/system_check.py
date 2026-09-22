"""OS/dependency detection & installer (Ticket 1.3).

- check_requirements(odoo_version) -> Result with a structured report:
    {"missing": [...], "present": [...], "python_version_ok": bool,
     "os": {...}, "odoo_version": str, "details": {...}, "requirements": [...]}
- install_requirements(missing, on_line=None, dry_run=False) -> Result,
  installing via pkexec (never raw sudo from a GUI app).

No GTK imports. Safe under pytest (dry_run=True never touches the system).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from odoo_vite.core import events
from odoo_vite.core.result import Result


# Verified 2026-09 (Phase 1.5 H.4) against Odoo's own sources. Per-entry
# provenance in comments: [setup.py] = python_requires from that branch's
# setup.py; [docs] = odoo/documentation on_premise/source.rst ("Odoo requires
# Python X or later", "PostgreSQL supported versions: 12.0 or above");
# [requirements.txt] = that branch's requirements.txt pins the wheel, so the
# matching -dev headers are genuinely needed to build it.
# Node: Odoo publishes NO Node floor (no package.json engines field anywhere
# in the repo; docs don't pin it) — min_node below is our conservative floor
# for the rtlcss asset pipeline, kept explicitly as unverified-upstream.
VERSION_REQUIREMENTS: dict[str, dict[str, object]] = {
    # [setup.py] python_requires='>=3.7'; [docs] "Python 3.7 or later".
    # [requirements.txt] pins psycopg2 (needs libpq-dev), lxml (libxml2-dev,
    # libxslt1-dev), Pillow (libjpeg-dev), python-ldap (libsasl2-dev,
    # libldap2-dev). [docs] Postgres "12.0 or above".
    "15.0": {"min_python": (3, 7), "min_node": 14, "min_postgres": 12,
             "notes": "Python 3.7+, Node 14+ (conservative), PG 12+"},
    # Same provenance as 15.0: [setup.py] >=3.7, [docs] 3.7+, PG 12+.
    "16.0": {"min_python": (3, 7), "min_node": 16, "min_postgres": 12,
             "notes": "Python 3.7+, Node 16+ (conservative), PG 12+"},
    # [setup.py] python_requires='>=3.10'; [docs] "Minimum requirement
    # updated from Python 3.7 to Python 3.10"; PG 12+ per docs.
    "17.0": {"min_python": (3, 10), "min_node": 16, "min_postgres": 12,
             "notes": "Python 3.10+, Node >= 16 (conservative), PG 12+"},
    # [setup.py] python_requires='>=3.10'; [docs] "Python 3.10 or later";
    # PG 12+ per docs. requirements markers still carry 3.10 branches.
    "18.0": {"min_python": (3, 10), "min_node": 18, "min_postgres": 12,
             "notes": "Python 3.10+, Node >= 18 (conservative), PG 12+"},
}


@dataclass
class Requirement:
    name: str
    label: str
    apt_packages: list[str]
    hint: str = ""


REQUIREMENTS: list[Requirement] = [
    Requirement("python3", "Python 3 interpreter", ["python3"], "apt install python3"),
    Requirement("python3-venv", "Python venv module", ["python3-venv"], "apt install python3-venv"),
    Requirement("python3-pip", "Python pip", ["python3-pip"], "apt install python3-pip"),
    Requirement("git", "Git VCS", ["git"], "apt install git"),
    Requirement("postgresql-server", "PostgreSQL server", ["postgresql"], "apt install postgresql"),
    Requirement("postgresql-client", "PostgreSQL client (psql)", ["postgresql-client"], "apt install postgresql-client"),
    Requirement("libpq-dev", "libpq headers (psycopg2 build)", ["libpq-dev"], "apt install libpq-dev"),
    Requirement("wkhtmltopdf", "wkhtmltopdf (PDF reports)", ["wkhtmltopdf"], "apt install wkhtmltopdf"),
    Requirement("node", "Node.js runtime", ["nodejs"], "apt install nodejs (>=16 for Odoo 17+)"),
    Requirement("npm", "npm package manager", ["npm"], "apt install npm"),
    Requirement("rtlcss", "rtlcss (Odoo asset pipeline, via npm)", ["node-rtlcss"], "npm install -g rtlcss"),
    Requirement("build-essential", "C build tools", ["build-essential"], "apt install build-essential"),
    Requirement("libxml2-dev", "libxml2 headers (lxml build)", ["libxml2-dev"], "apt install libxml2-dev"),
    Requirement("libxslt1-dev", "libxslt headers (lxml build)", ["libxslt1-dev"], "apt install libxslt1-dev"),
    Requirement("libjpeg-dev", "libjpeg headers (Pillow build)", ["libjpeg-dev"], "apt install libjpeg-dev"),
    Requirement("libsasl2-dev", "libsasl2 headers (python-ldap)", ["libsasl2-dev"], "apt install libsasl2-dev"),
    Requirement("libldap2-dev", "libldap headers (python-ldap)", ["libldap2-dev"], "apt install libldap2-dev"),
    Requirement("libssl-dev", "libssl headers", ["libssl-dev"], "apt install libssl-dev"),
    Requirement("zlib1g-dev", "zlib headers", ["zlib1g-dev"], "apt install zlib1g-dev"),
]


def get_os_info() -> dict[str, str]:
    info: dict[str, str] = {"name": "", "version": "", "id": "", "pretty": ""}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("NAME="):
                    info["name"] = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("VERSION_ID="):
                    info["version"] = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("ID="):
                    info["id"] = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("PRETTY_NAME="):
                    info["pretty"] = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        info["pretty"] = "Unknown (no /etc/os-release)"
    return info


def _dpkg_installed(package: str) -> bool:
    """True if dpkg reports the package as installed."""
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0 and "install ok installed" in proc.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _check_one(name: str) -> bool:
    if name == "python3":
        return _has_binary("python3")
    if name == "python3-venv":
        # venv module present OR apt package installed
        try:
            import venv  # noqa: F401  (stdlib on most systems)

            mod_ok = True
        except ImportError:
            mod_ok = False
        ensurepip_ok = _has_binary("python3") and _venv_ensurepip_ok()
        return (mod_ok and ensurepip_ok) or _dpkg_installed("python3-venv")
    if name == "python3-pip":
        try:
            import pip  # noqa: F401

            return True
        except ImportError:
            pass
        return _has_binary("pip3") or _dpkg_installed("python3-pip")
    if name == "git":
        return _has_binary("git")
    if name == "postgresql-server":
        return (
            _has_binary("pg_ctlcluster")
            or _has_binary("postgres")
            or _dpkg_installed("postgresql")
        )
    if name == "postgresql-client":
        return _has_binary("psql") or _dpkg_installed("postgresql-client")
    if name == "node":
        return _has_binary("node") or _has_binary("nodejs")
    if name == "npm":
        return _has_binary("npm")
    if name == "rtlcss":
        if _has_binary("rtlcss"):
            return True
        # also accept a global npm install without a linked bin on PATH
        try:
            proc = subprocess.run(
                ["npm", "ls", "-g", "rtlcss", "--depth=0"],
                capture_output=True, text=True, timeout=15,
            )
            return proc.returncode == 0 and "rtlcss" in proc.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    # Pure apt-package checks below
    apt_only = {
        "libpq-dev", "wkhtmltopdf", "build-essential", "libxml2-dev",
        "libxslt1-dev", "libjpeg-dev", "libsasl2-dev", "libldap2-dev",
        "libssl-dev", "zlib1g-dev",
    }
    if name in apt_only:
        if name == "wkhtmltopdf":
            return _has_binary("wkhtmltopdf") or _dpkg_installed("wkhtmltopdf")
        return _dpkg_installed(name)
    return False


def _venv_ensurepip_ok() -> bool:
    try:
        proc = subprocess.run(
            ["python3", "-m", "venv", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _pg_major() -> int | None:
    """Best-effort local Postgres major version (pg_config, else server probe)."""
    if _has_binary("pg_config"):
        try:
            proc = subprocess.run(
                ["pg_config", "--version"], capture_output=True,
                text=True, timeout=10)
            match = re.search(r"(\d+)", proc.stdout.strip())
            if match:
                return int(match.group(1))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if _has_binary("psql"):
        try:
            proc = subprocess.run(
                ["psql", "-h", "localhost", "-U", "postgres", "-d",
                 "postgres", "-tAc", "SHOW server_version"],
                capture_output=True, text=True, timeout=10)
            match = re.search(r"(\d+)", proc.stdout.strip())
            if proc.returncode == 0 and match:
                return int(match.group(1))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return None


def _node_major() -> int | None:
    for binary in ("node", "nodejs"):
        if not _has_binary(binary):
            continue
        try:
            proc = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            )
            match = re.search(r"v?(\d+)", proc.stdout.strip())
            if match:
                return int(match.group(1))
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return None


def check_requirements(odoo_version: str) -> Result:
    """Audit the system for an Odoo version's prerequisites.

    Always returns Result(ok=True, data=report) unless the audit itself
    crashes — a machine with missing packages is NOT a failure, it is a
    report with a non-empty "missing" list.
    """
    try:
        os_info = get_os_info()
        missing: list[str] = []
        present: list[str] = []
        details: dict[str, dict[str, object]] = {}
        for req in REQUIREMENTS:
            ok = _check_one(req.name)
            details[req.name] = {
                "label": req.label,
                "present": ok,
                "apt_packages": list(req.apt_packages),
                "hint": req.hint,
            }
            (present if ok else missing).append(req.name)

        matrix = VERSION_REQUIREMENTS.get(odoo_version, {})
        min_python = matrix.get("min_python", (3, 8))
        assert isinstance(min_python, tuple)
        python_version_ok = sys.version_info >= min_python  # type: ignore[operator]
        node_major = _node_major()
        min_node = matrix.get("min_node")
        node_ok: bool | None = None
        if isinstance(min_node, int):
            node_ok = (node_major is not None) and node_major >= min_node
        pg_major = _pg_major()
        min_pg = matrix.get("min_postgres")
        pg_ok: bool | None = None
        if isinstance(min_pg, int):
            pg_ok = (pg_major is not None) and pg_major >= min_pg

        report = {
            "odoo_version": odoo_version,
            "os": os_info,
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "python_version_ok": bool(python_version_ok),
            "min_python": ".".join(map(str, min_python)),
            "node_version": node_major,
            "node_ok": node_ok,
            "pg_version": pg_major,
            "pg_ok": pg_ok,
            "missing": missing,
            "present": present,
            "details": details,
            "version_notes": matrix.get("notes", ""),
            "all_ok": not missing and bool(python_version_ok),
        }
        summary = (
            "All requirements satisfied"
            if report["all_ok"]
            else f"Missing {len(missing)} requirement(s): {', '.join(missing)}"
        )
        return Result.success(data=report, message=summary)
    except Exception as exc:  # audit must never raise into the UI
        return Result.failure(f"System check failed: {exc}")


def _apt_packages_for(missing: list[str]) -> list[str]:
    wanted: list[str] = []
    by_name = {r.name: r for r in REQUIREMENTS}
    for name in missing:
        req = by_name.get(name)
        if req is None:
            continue
        for pkg in req.apt_packages:
            if pkg not in wanted:
                # rtlcss comes from npm, not apt — handled as a hint
                if name == "rtlcss" and pkg == "node-rtlcss":
                    continue
                wanted.append(pkg)
    return wanted


def install_requirements(
    missing: list[str],
    on_line: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> Result:
    """Install missing requirements via pkexec + apt-get (GUI-safe escalation).

    Streams each output line to on_line and the events bus ("apt-log").
    dry_run=True only builds the command (safe for tests / previews).
    """
    if not missing:
        return Result.success(message="Nothing to install")
    packages = _apt_packages_for(missing)
    npm_hint = "rtlcss" in missing
    if not packages and not npm_hint:
        return Result.failure(f"Unknown requirement names: {missing}")
    if not packages and npm_hint:
        return Result.success(
            data={"npm_command": "npm install -g rtlcss"},
            message="rtlcss must be installed via npm: run 'npm install -g rtlcss'",
        )
    cmd = ["pkexec", "apt-get", "install", "-y", *packages]
    if "rtlcss" in missing:
        pass  # apt part still runs; npm hint appended to the message
    if dry_run:
        return Result.success(
            data={"command": cmd, "packages": packages},
            message="Dry run: " + " ".join(cmd),
        )
    if shutil.which("pkexec") is None:
        return Result.failure(
            "pkexec not found — cannot escalate privileges from the GUI. "
            "Please run: sudo apt-get install -y " + " ".join(packages)
        )

    def _feed(line: str) -> None:
        events.emit("apt-log", line)
        if on_line:
            on_line(line)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _feed(line.rstrip("\n"))
        proc.wait(timeout=1800)
        if proc.returncode == 0:
            msg = "Installation complete"
            if npm_hint:
                msg += "; also run 'npm install -g rtlcss' for RTL stylesheet support"
            return Result.success(data={"packages": packages}, message=msg)
        return Result.failure(
            f"apt-get exited with code {proc.returncode} for: {' '.join(packages)}"
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Result.failure(f"Installer failed to start: {exc}")
