"""Streaming subprocess runner with cancellation (Sprint 2 shared helper).

Used by git_manager.clone_instance and venv_manager so both stream
stdout/stderr line-by-line to a progress callback and can be cancelled
mid-flight (watcher thread calls terminate(); no orphaned processes).

No GTK imports. Safe under pytest.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable

from odoo_vite.core.result import Result


def run_streaming(
    cmd: list[str],
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: int = 1800,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    """Run cmd, streaming each output line to progress_cb.

    data = {"lines": [...], "returncode": int, "cancelled": bool}.
    Non-zero exit (incl. cancellation) returns ok=False.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return Result.failure(f"Cannot start '{cmd[0]}': {exc}")

    if cancel is not None:

        def _watch() -> None:
            while proc.poll() is None:
                try:
                    if cancel():
                        proc.terminate()
                        break
                except Exception:
                    break
                time.sleep(0.2)

        threading.Thread(target=_watch, daemon=True).start()

    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            text = raw.rstrip("\n")
            lines.append(text)
            if progress_cb is not None:
                try:
                    progress_cb(text)
                except Exception:
                    pass
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return Result.failure(
            f"Timed out after {timeout}s: {' '.join(cmd)}",
            data={"lines": lines, "returncode": -1, "cancelled": False},
        )
    except OSError as exc:
        return Result.failure(
            f"Process I/O error: {exc}",
            data={"lines": lines, "returncode": -1, "cancelled": False},
        )

    rc = proc.returncode if proc.returncode is not None else -1
    was_cancelled = bool(cancel is not None and _cancel_now(cancel))
    if was_cancelled:
        return Result.failure(
            f"Cancelled by user: {' '.join(cmd)}",
            data={"lines": lines, "returncode": rc, "cancelled": True},
        )
    if rc != 0:
        tail = "\n".join(lines[-5:]) if lines else "(no output)"
        return Result.failure(
            f"Command failed (exit {rc}): {' '.join(cmd)}\n{tail}",
            data={"lines": lines, "returncode": rc, "cancelled": False},
        )
    return Result.success(
        data={"lines": lines, "returncode": 0, "cancelled": False},
        message="Command completed",
    )


def _cancel_now(cancel: Callable[[], bool]) -> bool:
    try:
        return bool(cancel())
    except Exception:
        return False
