"""Python venv creation + Odoo pip install (Sprint 2, Ticket 2.4).

- create_venv(instance_path, ...) — `python3 -m venv <instance_path>/venv`
- install_requirements(venv_path, community_path, ...) —
  `<venv>/bin/pip install --upgrade pip`, then
  `<venv>/bin/pip install -r <community_path>/requirements.txt`

Both stream output line-by-line to progress_cb and honour `cancel`.
On pip failure the last ~20 lines are surfaced in Result.message so the
wizard can show something actionable. No GTK imports.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from odoo_vite.core.proc import run_streaming
from odoo_vite.core.result import Result

VENV_DIRNAME = "venv"


def _pip(venv: Path) -> Path:
    return venv / "bin" / "pip"


def _ensure_packaging(pip: Path, progress_cb=None, cancel=None,
                      strict: bool = False) -> Result:
    """pip + setuptools + wheel inside the venv (H-B1).

    Python 3.12+ venvs ship pip only; Odoo (15.0's module.py line 9, 19.0,
    …) hard-imports pkg_resources at startup. setuptools>=81 REMOVED the
    pkg_resources API, so we pin setuptools<81 (last line shipping it).
    Non-strict (provisioning): warn-and-continue, the requirements step
    surfaces real failures. Strict (repair): any failure is the Result.
    """
    up = run_streaming(
        [str(pip), "install", "--upgrade", "pip"],
        progress_cb=progress_cb, cancel=cancel, timeout=600,
    )
    if not up.ok and progress_cb is not None:
        progress_cb(f"WARNING: pip self-upgrade failed, continuing: {up.message}")
    res = run_streaming(
        [str(pip), "install", "--upgrade", "setuptools<81", "wheel"],
        progress_cb=progress_cb, cancel=cancel, timeout=600,
    )
    if not res.ok:
        msg = (f"setuptools/wheel install failed: {res.message}\n"
               "Odoo versions that import pkg_resources at startup "
               "(15.0, 19.0, …) will crash without it.")
        if strict:
            return Result.failure(msg)
        if progress_cb is not None:
            progress_cb("WARNING: " + msg.split("\n")[0] + " — continuing")
    return Result.success(message="packaging tools ready")


def create_venv(
    instance_path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Result:
    """Create `<instance_path>/venv` with the system python3."""
    if shutil.which("python3") is None:
        return Result.failure("python3 not found (required to create the venv)")
    base = Path(instance_path).expanduser()
    venv_path = base / VENV_DIRNAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot create instance folder {base}: {exc}")
    res = run_streaming(
        ["python3", "-m", "venv", str(venv_path)],
        progress_cb=progress_cb, cancel=cancel, timeout=600,
    )
    if not res.ok:
        return Result.failure(
            f"venv creation failed: {res.message}\n"
            "Hint: install the venv module with "
            "'pkexec apt-get install -y python3-venv'.",
            data={"venv_path": str(venv_path), **(res.data or {})},
        )
    return Result.success(
        data={"venv_path": str(venv_path)},
        message=f"Virtualenv created at {venv_path}",
    )


def install_requirements(
    venv_path: str | Path,
    community_path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Result:
    """pip-install the cloned Odoo version's requirements into the venv."""
    venv = Path(venv_path).expanduser()
    pip = venv / "bin" / "pip"
    if not pip.exists():
        return Result.failure(
            f"pip not found at {pip} — the venv looks incomplete; "
            "re-run provisioning (venv step will be retried)."
        )
    req_file = Path(community_path).expanduser() / "requirements.txt"
    if not req_file.is_file():
        return Result.failure(
            f"{req_file} missing — the community clone looks incomplete; "
            "re-run provisioning (clone step will be retried)."
        )

    _ensure_packaging(pip, progress_cb=progress_cb, cancel=cancel,
                      strict=False)

    res = run_streaming(
        [str(pip), "install", "-r", str(req_file)],
        progress_cb=progress_cb, cancel=cancel, timeout=3600,
    )
    if not res.ok:
        lines = (res.data or {}).get("lines", []) if res.data else []
        tail = "\n".join(lines[-20:]) if lines else "(no pip output captured)"
        return Result.failure(
            f"pip install of Odoo requirements failed.\n--- last ~20 lines ---\n{tail}\n"
            "Hint: most build failures mean a missing system library — re-run "
            "the Sprint 1 system check (libpq-dev, libxml2-dev, libxslt1-dev, "
            "libjpeg-dev, libsasl2-dev, libldap2-dev, ...).",
            data={"venv_path": str(venv), **(res.data or {})},
        )
    return Result.success(
        data={"venv_path": str(venv), "requirements": str(req_file)},
        message="Odoo Python requirements installed into the venv",
    )


def repair_venv(instance_id: str, progress_cb=None, cancel=None,
                db_path=None) -> Result:
    """Install setuptools/wheel/pip into an EXISTING venv (H-B1 repair).

    For instances provisioned before the packaging fix: no clone, no
    re-provisioning — just closes the two-package gap, then verifies
    `import pkg_resources` inside that venv.
    """
    from odoo_vite.core.registry import get_instance

    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    venv = Path(inst.venv_path) if inst.venv_path else None
    pip = venv / "bin" / "pip" if venv else None
    python = venv / "bin" / "python" if venv else None
    if not pip or not pip.exists():
        if not inst.venv_path:
            return Result.failure(
                f"No Python environment recorded for '{inst.name}' — "
                "set its venv path first, then repair")
        return Result.failure(
            f"pip not found at {pip} — the venv is missing, not just "
            "outdated; re-run provisioning instead")
    if python and not python.exists():
        return Result.failure(f"venv python missing at {python}")

    strict = _ensure_packaging(pip, progress_cb=progress_cb, cancel=cancel,
                               strict=True)
    if not strict.ok:
        return strict
    verify = run_streaming(
        [str(python), "-c", "import pkg_resources; print('pkg_resources ok')"],
        progress_cb=progress_cb, cancel=cancel, timeout=120,
    )
    if not verify.ok:
        return Result.failure(
            f"Repair ran but pkg_resources still unimportable: {verify.message}")
    return Result.success(
        data={"venv_path": str(venv)},
        message=f"Venv repaired for '{inst.name}' (setuptools/wheel present)")
