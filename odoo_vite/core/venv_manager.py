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

    up = run_streaming(
        [str(pip), "install", "--upgrade", "pip"],
        progress_cb=progress_cb, cancel=cancel, timeout=600,
    )
    if not up.ok:
        # pip self-upgrade failing is usually non-fatal (offline mirror etc.);
        # log it and continue with the bundled pip.
        if progress_cb is not None:
            progress_cb(f"WARNING: pip self-upgrade failed, continuing: {up.message}")

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
