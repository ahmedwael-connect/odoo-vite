"""Interactive odoo-bin shell sessions (Sprint 11, Ticket 11.1).

Architecture decision (per spec's explicit question — decided with reasoning):
DIRECT SUBPROCESS with a PTY, not WebSocket. This is a single-machine GTK
desktop app: backend and UI share the process space, so a WebSocket hop
would add a server, ports, auth, and framing bugs for zero benefit (no
remote/web companion exists in scope).

Why a PTY and not pipes: odoo/cli/shell.py explicitly branches on
`os.isatty(sys.stdin)` — piped stdin gets `exec(sys.stdin.read())`
(script mode, then exit), and only a TTY gets the real interactive
`code.InteractiveConsole` REPL. Pipes cannot host this feature, period.
So: pty.openpty(), child on the slave end, our reader/writer on the master
end (binary, unbuffered, incremental UTF-8 decode). Output is terminal-
authentic (echo + prompts included); the UI renders it raw.

Validate-before-connect reuses db_state (initialized?), the venv/python
checks, and conf validation — a missing prerequisite fails with its specific
message, never a raw connection error. No GTK imports.
"""

from __future__ import annotations

import codecs
import os
import pty
import subprocess
import threading
from collections import deque
from pathlib import Path

from odoo_vite.core.result import Result


class OdooShell:
    """One interactive shell session. UI drives start/send/drain/stop."""

    def __init__(self, max_lines: int = 2000) -> None:
        self._proc: subprocess.Popen | None = None
        self._master: int | None = None
        self._reader: threading.Thread | None = None
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._exited: int | None = None

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        proc = self._proc
        if proc is None:
            return False
        if self._exited is not None:
            return False
        if proc.poll() is not None:
            self._exited = proc.returncode
            return False
        return True

    @property
    def exit_code(self) -> int | None:
        self.running  # refresh liveness
        return self._exited

    # ------------------------------------------------------------------ control
    def start(self, instance, db_name: str = "", db_path=None) -> Result:  # type: ignore[no-untyped-def]
        """Validate everything, then spawn `odoo-bin shell` on a PTY."""
        from odoo_vite.core import conf_manager
        from odoo_vite.core.db_state import get_db_state
        from odoo_vite.core.instance import effective_python
        from odoo_vite.core.registry import get_db_password

        if self.running:
            return Result.failure("A shell session is already running")
        target = ((db_name or instance.primary_db) or "").strip()
        if not target:
            return Result.failure("No database selected for the shell")
        python = (effective_python(instance) or "").strip()
        if not python or not Path(python).is_file():
            return Result.failure(
                f"Venv python missing at {python or '(no interpreter recorded)'}")
        odoo_bin = Path(instance.community_path or "") / "odoo-bin"
        if not odoo_bin.is_file():
            return Result.failure(f"odoo-bin missing at {odoo_bin}")
        if instance.conf_path:
            conf = conf_manager.read_conf(instance.conf_path)
            if not conf.ok:
                return Result.failure(f"Conf invalid, shell refused: {conf.message}")
        try:
            pw = get_db_password(instance) or None
        except Exception:
            pw = None
        state = get_db_state(target, instance.db_user or "odoo", pw)
        if state.error and not state.exists:
            return Result.failure(f"Cannot inspect '{target}': {state.error}")
        if not state.exists:
            return Result.failure(
                f"Database '{target}' does not exist — initialize it first")
        if not state.initialized:
            return Result.failure(
                f"Database '{target}' is not initialized — run Initialize first; "
                "the shell needs installed base tables (env[] lookups)")

        cmd = [python, str(odoo_bin), "shell",
               "-c", instance.conf_path, "-d", target]
        try:
            master, slave = pty.openpty()
        except OSError as exc:
            return Result.failure(f"Cannot allocate a terminal: {exc}")
        try:
            proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, close_fds=True)
        except OSError as exc:
            try:
                os.close(master)
            except OSError:
                pass
            return Result.failure(f"Cannot spawn odoo-bin shell: {exc}")
        finally:
            try:
                os.close(slave)
            except OSError:
                pass
        self._proc = proc
        self._master = master
        self._exited = None
        with self._lock:
            self._lines.clear()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()
        return Result.success(
            data={"pid": proc.pid, "database": target},
            message=f"Shell ready on '{target}' (pid {proc.pid})")

    def _drain(self) -> None:
        assert self._master is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = ""
        while True:
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                break
            if not chunk:
                break
            text += decoder.decode(chunk)
            *complete, text = text.split("\n")
            with self._lock:
                for ln in complete:
                    self._lines.append(ln.rstrip("\r"))
        try:
            tail = decoder.decode(b"", final=True)
            if tail:
                with self._lock:
                    self._lines.append(tail)
        except Exception:
            pass
        finally:
            try:
                if self._proc is not None:
                    self._exited = self._proc.wait(timeout=5)
            except Exception:
                pass

    def drain_output(self, max_lines: int = 500) -> list[str]:
        """Take up to max_lines of new output (UI polls this)."""
        with self._lock:
            out = [self._lines.popleft() for _ in range(min(len(self._lines), max_lines))]
        # refresh liveness as a side effect so exits surface promptly
        self.running
        return out

    def send_line(self, line: str) -> Result:
        """Send one input line. Returns failure (not exception) if dead."""
        if self._master is None or not self.running:
            return Result.failure("Shell is not running")
        try:
            os.write(self._master, (line if line.endswith("\n") else line + "\n").encode(
                "utf-8", errors="replace"))
        except OSError as exc:
            return Result.failure(f"Cannot write to shell: {exc}")
        return Result.success(message="sent")

    def stop(self) -> Result:
        """Terminate gracefully (SIGTERM → brief wait → SIGKILL), no orphans."""
        proc, master = self._proc, self._master
        self._proc = None
        self._master = None
        if proc is None:
            return Result.success(message="Shell was not running")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError as exc:
            return Result.failure(f"Cannot stop shell: {exc}")
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            self._exited = proc.returncode if proc.returncode is not None else -1
        return Result.success(message="Shell stopped")
