"""PSS-5a: module ops failure paths + Qt parity (no PG).

Framework-free by construction (imports core + ops.modules only — no
bridge/component/dialog imports): per docs/slint-thread-safety.md, worker
threads and Slint values never coexist in tests. asyncio.run() drives the
coroutines on the main thread.
"""

import asyncio



from odoo_vite.core.result import Result  # noqa: E402
from odoo_vite.ops.modules import (  # noqa: E402
    ModuleOps,
    preview_command,
    split_deps,
    state_category,
)


def _ops():
    messages, refreshes = [], []
    mods, deps = [], []
    ops = ModuleOps(lambda m, k="info": messages.append((m, k)),
                    lambda: refreshes.append(1),
                    lambda i, m, d, e: mods.append((i, m, d, e)),
                    lambda i, n, d, r: deps.append((i, n, d, r)))
    return ops, messages, refreshes, mods, deps


def _mod(**overrides):
    base = {"name": "sale", "state": "installed",
            "installed_version": "17.0.1.0",
            "available_version": "17.0.1.0", "summary": "Sales"}
    base.update(overrides)
    return base


def test_state_category_matches_qt():
    """State rule, frozen at cutover (was byte-parity-tested against the
    Qt view until ui_qt/ was deleted in PSS-9)."""
    assert state_category(_mod()) == "Installed"
    assert state_category(_mod(available_version="17.0.9.9")) == \
        "Upgradeable"
    assert state_category(_mod(state="to install")) == "Upgradeable"
    assert state_category(_mod(state="to upgrade")) == "Upgradeable"
    assert state_category(_mod(state="uninstalled")) == "Installable"
    assert state_category(_mod(state="")) == "Installable"
    assert state_category(
        _mod(installed_version="", available_version="")) == "Installed"


def test_split_deps():
    edges = [["sale", "base"], ["sale", "product"], ["purchase", "sale"]]
    assert split_deps(edges, "sale") == (["base", "product"], ["purchase"])
    assert split_deps(edges, "base") == ([], ["sale"])
    assert split_deps(edges, "ghost") == ([], [])


def test_preview_command_shape(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "prev.db"))
    inst = Instance(name="Prev", version="17.0", path=str(tmp_path),
                    venv_path=str(tmp_path / "venv"),
                    community_path=str(tmp_path / "odoo"),
                    conf_path=str(tmp_path / "odoo.conf"),
                    primary_db="main")
    assert create_instance(inst).ok
    cmd = preview_command(inst, "main", "-i", ["sale", "purchase"])
    assert cmd[-3:] == ["-i", "sale,purchase", "--stop-after-init"]
    assert cmd[0] == f"{tmp_path}/venv/bin/python"
    assert cmd[1] == f"{tmp_path}/odoo/odoo-bin"


def test_unknown_ids_fail_quietly():
    ops, messages, refreshes, mods, deps = _ops()
    modules, diff, error = asyncio.run(ops.refresh_modules("no-such-id"))
    assert (modules, diff) == ([], {})
    assert "not found" in error
    assert mods == [("no-such-id", [], {}, "Instance not found")]
    assert asyncio.run(ops.fetch_deps("no-such-id", "sale")) == ([], [])
    res = asyncio.run(ops.install("no-such-id", ["sale"]))
    assert not res.ok
    res = asyncio.run(ops.update("no-such-id", ["sale"]))
    assert not res.ok
    res = asyncio.run(ops.uninstall("no-such-id", "sale"))
    assert not res.ok
    res = asyncio.run(ops.update_code("no-such-id"))
    assert not res.ok
    res = asyncio.run(ops.run_tests("no-such-id", "testdb", "sale"))
    assert not res.ok
    assert any("disappeared" in m or "not found" in m
               for m, _k in messages)
    # Early bails never touch the registry: no refresh for no-ops.
    assert refreshes == []


def test_empty_selections_refused(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "empty.db"))
    inst = Instance(name="Empty", version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    ops, messages, refreshes, _, _ = _ops()
    assert not asyncio.run(ops.install(inst.id, [])).ok
    assert not asyncio.run(ops.update(inst.id, ["  "])).ok
    assert not asyncio.run(ops.uninstall(inst.id, "")).ok
    assert messages  # each refusal explains itself
    assert refreshes == []


def test_no_primary_db_refused(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "nodb.db"))
    inst = Instance(name="NoDB", version="17.0", path=str(tmp_path))
    assert create_instance(inst).ok
    ops, messages, _, mods, _ = _ops()
    _, _, error = asyncio.run(ops.refresh_modules(inst.id))
    assert "primary" in error
    assert mods[0][3] and "primary" in mods[0][3]
    assert not asyncio.run(ops.install(inst.id, ["sale"])).ok
    assert any("primary" in m for m, _k in messages)


def test_update_code_guards(tmp_path, monkeypatch):
    """Running / unconfigured / non-git instances fail without side effects."""
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance, update_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "uc.db"))
    run = Instance(name="Run", version="17.0", path=str(tmp_path),
                   status="running", auto_update_modules=["sale"])
    assert create_instance(run).ok
    ops, messages, _, _, _ = _ops()
    res = asyncio.run(ops.update_code(run.id))
    assert not res.ok and "Stop" in res.message

    bare = Instance(name="Bare", version="17.0", path=str(tmp_path),
                    community_path=str(tmp_path / "odoo"))
    assert create_instance(bare).ok
    res = asyncio.run(ops.update_code(bare.id))
    assert not res.ok and "auto-update" in res.message

    nongit = Instance(name="NonGit", version="17.0", path=str(tmp_path),
                      community_path=str(tmp_path / "plain"),
                      auto_update_modules=["sale"])
    assert create_instance(nongit).ok
    (tmp_path / "plain").mkdir()
    res = asyncio.run(ops.update_code(nongit.id))
    assert not res.ok and "git" in res.message
    assert update_instance(run.id, status="stopped").ok  # leave clean


def test_run_tests_refuses_primary_db(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "t.db"))
    inst = Instance(name="T", version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    ops, _, _, _, _ = _ops()
    res = asyncio.run(ops.run_tests(inst.id, "main", "sale"))
    assert not res.ok and "PRIMARY" in res.message


def test_install_progress_passthrough(tmp_path, monkeypatch):
    """progress_cb + cancel reach core (bridge progress plumbing relies)."""
    import threading

    from odoo_vite.core import module_manager
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "pt.db"))
    inst = Instance(name="PT", version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    seen = {}

    def _fake_install(inst_arg, db, mods, progress_cb=None, cancel=None):
        seen["off_main"] = (
            threading.current_thread() is not threading.main_thread())
        seen["cancel"] = cancel() if cancel is not None else None
        if progress_cb is not None:
            progress_cb("streamed")
        return Result(ok=True, message="Installed sale", data={})

    monkeypatch.setattr(module_manager, "install_modules", _fake_install)
    ops, messages, refreshes, _, _ = _ops()
    lines = []
    res = asyncio.run(ops.install(inst.id, ["sale"],
                                  progress_cb=lines.append,
                                  cancel=lambda: False))
    assert res.ok
    assert seen == {"off_main": True, "cancel": False}
    assert lines == ["streamed"]
    assert messages == [("Installed sale", "info")] and refreshes == [1]


def test_refresh_sink_receives_list_and_diff(tmp_path, monkeypatch):
    from odoo_vite.core import module_manager
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "rf.db"))
    inst = Instance(name="RF", version="17.0", path=str(tmp_path),
                    primary_db="main")
    assert create_instance(inst).ok
    monkeypatch.setattr(
        module_manager, "list_modules",
        lambda *a, **k: Result(ok=True, message="2",
                               data={"modules": [_mod()], "database": "x"}))
    monkeypatch.setattr(
        module_manager, "diff_modules",
        lambda *a, **k: Result(ok=True, message="1",
                               data={"diff": [{"name": "sale",
                                               "status": "disk-newer",
                                               "note": "n"}],
                                     "database": "x"}))
    ops, _, _, mods, _ = _ops()
    modules, diff, error = asyncio.run(ops.refresh_modules(inst.id))
    assert error == ""
    assert [m["name"] for m in modules] == ["sale"]
    assert diff["sale"]["status"] == "disk-newer"
    assert mods[0][0] == inst.id and mods[0][3] == ""
