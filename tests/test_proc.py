"""3.3.0 P2: run_streaming must honor its timeout even when the child is
silent (the old main-thread reader loop blocked until EOF, so the
TimeoutExpired branch could never fire)."""

import sys
import time

from odoo_vite.core.proc import run_streaming


def test_timeout_kills_a_silent_hung_child():
    start = time.monotonic()
    res = run_streaming(
        [sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    elapsed = time.monotonic() - start
    assert not res.ok and "Timed out after 1s" in res.message
    assert res.data["returncode"] == -1
    assert elapsed < 10, f"timeout did not fire (took {elapsed:.1f}s)"


def test_streams_lines_and_succeeds():
    seen: list[str] = []
    res = run_streaming(
        [sys.executable, "-c", "print('one'); print('two')"],
        progress_cb=seen.append, timeout=30)
    assert res.ok, res.message
    assert res.data["lines"] == ["one", "two"]
    assert seen == ["one", "two"]
    assert res.data["returncode"] == 0


def test_nonzero_exit_returns_tail():
    res = run_streaming(
        [sys.executable, "-c", "print('last line'); raise SystemExit(3)"],
        timeout=30)
    assert not res.ok and "exit 3" in res.message
    assert "last line" in res.message
    assert res.data["returncode"] == 3


def test_cancel_terminates_child():
    res = run_streaming(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cancel=lambda: True, timeout=60)
    assert not res.ok and "Cancelled by user" in res.message
    assert res.data["cancelled"] is True


def test_stdin_text_is_fed_and_visible():
    res = run_streaming(
        [sys.executable, "-c", "import sys; print('got:' + sys.stdin.read())"],
        stdin_text="hello", timeout=30)
    assert res.ok, res.message
    assert any("got:hello" in ln for ln in res.data["lines"])
