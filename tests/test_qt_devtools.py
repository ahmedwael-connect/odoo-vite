"""PSQ-8: DevTools view + flows (offscreen, pytest-qt).

Record-browser guardrails locked: typed delete, diff preview, ir.*
refusal. DevMode dotfile filtering checked against a REAL deep path
(GTK lesson: synthetic /a/... paths hid the ~/.local rejection bug).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.flows.dev_tools_process import (  # noqa: E402
    DevToolsProcessFlows,
)
from odoo_vite.ui_qt.flows.dev_tools_rpc import (  # noqa: E402
    DevToolsRpcFlows,
    diff_record_values,
)
from odoo_vite.ui_qt.views.devtools import DevToolsPage  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-dev.db"))


def _inst_dict():
    return {"id": "d1", "name": "Demo", "status": "stopped",
            "version": "17.0"}


def test_diff_preview_semantics():
    assert diff_record_values({"a": 1, "b": "x"}, {"a": 1, "b": "y"}) == {
        "b": ("x", "y")}
    assert diff_record_values({"a": 1}, {"a": 1}) == {}
    assert diff_record_values(None, {"a": 1}) == {"a": (None, 1)}


def test_devtools_view_emits(qapp, qtbot):
    page = DevToolsPage()
    qtbot.addWidget(page)
    page.show_instance(_inst_dict())
    fired = []
    page.actionRequested.connect(lambda a, i, p: fired.append((a, i, p)))
    page.btn_cron_refresh.click()
    assert fired[-1] == ("cron-refresh", "d1", None)
    page.btn_rec_delete.click()
    assert fired[-1] == ("rec-delete", "d1", None)
    page.entry_test_module.setText("sale")
    page.entry_test_db.setText("t_db")
    page.btn_test_run.click()
    assert fired[-1] == ("test-run", "d1", {"module": "sale", "db": "t_db"})
    page.check_devmode.setChecked(True)
    assert fired[-1] == ("devmode", "d1", True)
    page.entry_shell_in.setText("1+1")
    page._on_shell_send()
    assert fired[-1] == ("shell-send", "d1", "1+1")
    assert page.entry_shell_in.text() == ""


def test_rpc_flows_need_connection(qapp, qtbot):
    flows = DevToolsRpcFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.list_models("no-such-id")
    flows.cron_refresh("no-such-id")
    qtbot.wait(500)
    assert messages.count("Connect RPC first") == 2


def test_rpc_connect_no_password(qapp, qtbot):
    flows = DevToolsRpcFlows()
    messages = []
    flows.message.connect(messages.append)
    # Unknown instance: silent early return (GTK parity — the empty
    # password prompt only fires for a real instance).
    flows.connect("no-such-id", "admin", "", False)
    qtbot.wait(500)
    assert messages == []


def test_rec_delete_refuses_ir_models(qapp, qtbot):
    flows = DevToolsRpcFlows()
    flows._sessions["d1"] = {"url": "u", "db": "d", "uid": 1,
                             "password": "p"}
    flows._browser["d1"] = {"model": "ir.model", "offset": 0, "cache": [
        {"id": 5, "display_name": "X"}]}
    messages = []
    flows.message.connect(messages.append)
    flows.rec_delete(None, "d1", 5)
    assert any("ir.model" in m and "off-limits" in m for m in messages)


def test_rec_edit_needs_selection(qapp, qtbot):
    flows = DevToolsRpcFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.rec_edit(None, "d1", 99)
    assert messages == ["Select a record first"]


def test_process_flows_fail_safe(qapp, qtbot):
    flows = DevToolsProcessFlows()
    messages = []
    flows.message.connect(messages.append)
    flows.shell_send("no-such-id", "1+1")
    assert messages == ["Shell is not running"]
    flows.test_run("no-such-id", "", "")
    assert len(messages) == 1  # unknown instance: silent return
    flows.devmode("no-such-id", True)
    qtbot.wait(500)
    assert len(messages) == 1  # unknown instance: silent return


def test_dotfile_filtering_real_deep_path(qapp, qtbot, tmp_path):
    """GTK lesson: ~/.local rejection hid behind synthetic test paths.
    Build a real deep tree with dotfiles, junk dirs, and real sources."""
    from odoo_vite.core import devwatch

    root = tmp_path / ".local" / "share" / "proj" / "addons" / "mod_a"
    (root / "models").mkdir(parents=True)
    (root / "__init__.py").write_text("x")
    (root / "models" / "m.py").write_text("y")
    (root / ".hidden.py").write_text("z")
    junk = root / "__pycache__"
    junk.mkdir()
    (junk / "c.pyc").write_bytes(b"0")
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text("r")

    flows = DevToolsProcessFlows()
    dirs = flows._collect_watch_dirs([str(tmp_path / ".local" / "share"
                                          / "proj" / "addons")])
    assert str(root) in dirs
    assert str(root / "models") in dirs
    assert not [d for d in dirs if "__pycache__" in d or "/.git" in d]
    # Dotfile PARENT accepted (the Sprint 11 bug), dot-LEAF rejected.
    assert devwatch.should_watch(str(root / "models" / "m.py"))
    assert not devwatch.should_watch(str(root / ".hidden.py"))
    assert not devwatch.should_watch(str(junk / "c.pyc"))
    assert "addons" not in devwatch.SKIP_DIRS  # real dirs never skipped
