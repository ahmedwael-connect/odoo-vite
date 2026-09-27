"""py-spy flame graphs against running instances (Sprint 8, Ticket B.5).

Privilege design (per spec: reuse the established pattern): attaching to
another process needs ptrace rights. Try DIRECT first (same-user attach
often works depending on kernel.yama.ptrace_scope); on permission-denied
output, fall back to the pkexec path already used for system operations.
No new escalation mechanism invented.

py-spy itself is a third-party tool we don't bundle: detected via PATH,
installable with user-scoped `pip install py-spy` (no pkexec needed for
that step) — offered from the UI when missing.

No GTK imports.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import psutil

from odoo_vite.core.result import Result

DEFAULT_DURATION = 10
MIN_DURATION = 5
MAX_DURATION = 120


def py_spy_path() -> str | None:
    """Locate py-spy (PATH + common user-install locations)."""
    found = shutil.which("py-spy")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "py-spy",
                      Path("/usr/local/bin/py-spy")):
        if candidate.is_file():
            return str(candidate)
    return None


def ensure_py_spy(progress_cb: Callable[[str], None] | None = None) -> Result:
    """Make sure py-spy is usable, installing user-scoped pip package if not."""
    found = py_spy_path()
    if found:
        return Result.success(data={"path": found}, message="py-spy ready")
    if shutil.which("pip3") is None and shutil.which("pip") is None:
        return Result.failure(
            "py-spy is not installed and no pip is available. Install it with: "
            "pip install py-spy (https://github.com/benfred/py-spy)")
    from odoo_vite.core.proc import run_streaming

    pip = shutil.which("pip3") or shutil.which("pip")
    res = run_streaming([pip, "install", "--user", "py-spy"],
                        progress_cb=progress_cb, timeout=600)
    if not res.ok:
        return Result.failure(
            f"Could not install py-spy: {res.message}\nInstall it manually: "
            "pip install py-spy")
    found = py_spy_path()
    if not found:
        return Result.failure(
            "py-spy installed but not found on PATH — add ~/.local/bin to "
            "PATH and retry")
    return Result.success(data={"path": found}, message="py-spy installed")


def profile_pid(pid: int, duration: int = DEFAULT_DURATION,
                output_svg: str | Path = "", progress_cb=None,
                cancel=None, spawn: list[str] | None = None) -> Result:
    """Record a flame graph: `py-spy record -d N -o out.svg --pid P`.

    Direct first, pkexec fallback on permission errors. Validates the SVG.
    Optional spawn=[...]: profile a fresh child command instead of attaching
    (`py-spy record -- cmd…`) — children are always traceable, which makes
    the record→SVG→validate pipeline testable anywhere (and lets users
    profile ad-hoc commands, not just live PIDs).
    """
    duration = max(MIN_DURATION, min(int(duration or DEFAULT_DURATION),
                                     MAX_DURATION))
    spy = py_spy_path()
    if spy is None:
        return Result.failure(
            "py-spy is not installed — install it first (pip install py-spy)")
    dest = Path(output_svg).expanduser() if str(output_svg) else None
    if dest is None:
        return Result.failure("No destination SVG path given")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot create output folder: {exc}")

    from odoo_vite.core.proc import run_streaming

    if spawn:
        base = [spy, "record", "-d", str(duration), "-o", str(dest),
                "--", *[str(a) for a in spawn]]
        res = run_streaming(base, progress_cb=progress_cb, cancel=cancel,
                            timeout=duration + 120)
        if not res.ok:
            return Result.failure(f"py-spy spawn-record failed: {res.message}")
        return _validate_svg(dest, pid="<spawned>", duration=duration)

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return Result.failure(f"Invalid pid '{pid}'")
    try:
        if not psutil.pid_exists(pid):
            return Result.failure(f"Process {pid} is not running")
    except Exception as exc:
        return Result.failure(f"Cannot inspect pid {pid}: {exc}")

    base = [spy, "record", "-d", str(duration), "-o", str(dest),
            "--pid", str(pid)]
    res = run_streaming(base, progress_cb=progress_cb, cancel=cancel,
                        timeout=duration + 120)
    low = (res.message or "").lower()
    if not res.ok and ("permission denied" in low or "operation not permitted" in low
                       or "ptrace" in low):
        if progress_cb:
            progress_cb("Direct attach denied — retrying elevated (pkexec)…")
        if shutil.which("pkexec") is None:
            return Result.failure(
                "py-spy needs elevated ptrace rights here and pkexec is "
                "missing — run the profile as a privileged user instead")
        res = run_streaming(["pkexec", *base], progress_cb=progress_cb,
                            cancel=cancel, timeout=duration + 120)
        low = (res.message or "").lower()
        if not res.ok and "must be setuid" in low:
            return Result.failure(
                "Elevation is unavailable in this session (pkexec reports it "
                "is not setuid here — typical for containers/headless shells "
                "without a polkit agent). Profile from a desktop session, or run: "
                f"sudo env PATH=\"$PATH\" py-spy record -d {duration} "
                f"-o <out.svg> --pid {pid}")
    if not res.ok:
        return Result.failure(f"py-spy failed: {res.message}")
    return _validate_svg(dest, pid=pid, duration=duration)


def _validate_svg(dest: Path, pid, duration: int) -> Result:
    try:
        if not dest.is_file() or dest.stat().st_size == 0:
            return Result.failure("py-spy produced no output file")
        with open(dest, "rb") as fh:
            head = fh.read(4096)
        if b"<svg" not in head:
            return Result.failure("py-spy output is not a valid SVG")
    except OSError as exc:
        return Result.failure(f"Cannot read profile output: {exc}")
    return Result.success(data={"svg": str(dest), "pid": pid,
                                "duration": duration},
                          message=f"Flame graph saved to {dest}")
