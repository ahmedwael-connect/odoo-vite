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
    stdin_text: str | None = None,
) -> Result:
    """Run cmd, streaming each output line to progress_cb.

    data = {"lines": [...], "returncode": int, "cancelled": bool}.
    Non-zero exit (incl. cancellation) returns ok=False.
    stdin_text (Sprint 6): piped to the child's stdin (e.g. odoo-bin shell).
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=(subprocess.PIPE if stdin_text is not None else None),
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return Result.failure(f"Cannot start '{cmd[0]}': {exc}")

    if stdin_text is not None:
        def _feed_stdin() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except (OSError, ValueError, AssertionError):
                pass

        threading.Thread(target=_feed_stdin, daemon=True).start()

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
    io_errors: list[OSError] = []
    finished = threading.Event()

    def _pump() -> None:
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
        except OSError as exc:
            io_errors.append(exc)
        finally:
            finished.set()

    # 3.3.0 P2: the reader runs in its own thread. The old main-thread
    # `for raw in proc.stdout` blocked until EOF, so wait(timeout=...) only
    # ever ran AFTER the child was gone — a hung-but-silent child (git/pip
    # waiting on a prompt) could never time out and the TimeoutExpired
    # branch was dead code.
    threading.Thread(target=_pump, daemon=True).start()

    deadline = time.monotonic() + timeout
    while not finished.wait(0.2):
        if time.monotonic() >= deadline:
            _kill_quietly(proc)
            return Result.failure(
                f"Timed out after {timeout}s: {' '.join(cmd)}",
                data={"lines": lines, "returncode": -1, "cancelled": False},
            )
    if io_errors:
        return Result.failure(
            f"Process I/O error: {io_errors[0]}",
            data={"lines": lines, "returncode": -1, "cancelled": False},
        )
    # EOF seen; the child may still be exiting — bound the reap by the
    # same overall deadline (0 remaining → immediate timeout).
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_quietly(proc)
        return Result.failure(
            f"Timed out after {timeout}s: {' '.join(cmd)}",
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


def _kill_quietly(proc: subprocess.Popen) -> None:
    """Kill + reap, swallowing races (child already gone, etc.)."""
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass
