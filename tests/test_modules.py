"""Sprint 6 Part B core tests: module_manager (mocked pg/Popen) + scaffolder."""

import json

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance
from odoo_vite.core.result import Result


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_AUDIT", str(tmp_path / "audit.log"))
    return tmp_path / "reg.db"


def _inst(base, **overrides):
    kwargs = {"name": "M", "version": "17.0", "path": str(base),
              "venv_path": str(base / "venv"),
              "community_path": str(base / "community"),
              "conf_path": str(base / "odoo.conf"),
              "log_path": str(base / "logs" / "odoo.log"),
              "custom_addons_path": str(base / "custom_addons"),
              "port": 8094, "db_user": "odoo", "db_password": "odoo",
              "primary_db": "m_db", "status": "stopped", "db_created": True}
    kwargs.update(overrides)
    return Instance(**kwargs)


def _fs(base):
    (base / "venv" / "bin").mkdir(parents=True)
    (base / "venv" / "bin" / "python").touch()
    (base / "community").mkdir(parents=True)
    (base / "community" / "odoo-bin").touch()
    (base / "logs").mkdir(parents=True)
    (base / "odoo.conf").touch()
    (base / "custom_addons").mkdir(parents=True)


ROWS = [
    {"name": "sale", "state": "installed", "installed_version": "17.0.1",
     "available_version": "17.0.2", "summary": "Sales"},
    {"name": "stock", "state": "uninstalled", "installed_version": "",
     "available_version": "17.0.1", "summary": "Stock"},
]


def _row_line(r):
    return "\x1f".join([r["name"], r["state"], r["installed_version"],
                        r["available_version"], r["summary"]])


def _pg_ok(monkeypatch, rows=ROWS):
    import odoo_vite.core.module_manager as mm

    payload = "\n".join(_row_line(r) for r in rows)
    monkeypatch.setattr(mm, "_pg", lambda *a, **k: (0, payload))


def test_list_modules(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    base = tmp_path / "i"
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.list_modules(inst, "m_db", db_path=db)
    assert res.ok and len(res.data["modules"]) == 2
    assert res.data["modules"][0]["name"] == "sale"


def test_list_refuses_uninitialized(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(db_name="m_db"))
    base = tmp_path / "i"
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.list_modules(inst, "m_db", db_path=db)
    assert not res.ok and "nitializ" in res.message


def test_install_builds_command_and_guards(tmp_path, db, monkeypatch):
    import odoo_vite.core.proc as proc
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    launched = []

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0,
              cwd=None, env=None, stdin_text=None):
        launched.append(list(cmd))
        return Result.success(data={"lines": [], "returncode": 0,
                                    "cancelled": False})

    monkeypatch.setattr(proc, "run_streaming", _fake)
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: None)
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.install_modules(inst, "m_db", ["stock"])
    assert res.ok, res.message
    cmd = launched[0]
    assert cmd[cmd.index("-i") + 1] == "stock"
    assert "--stop-after-init" in cmd and "-d" in cmd
    assert not mm.install_modules(inst, "m_db", []).ok

    # running-on-target refuses
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: 4242)
    res = mm.install_modules(inst, "m_db", ["stock"])
    assert not res.ok and "4242" in res.message
    assert len(launched) == 1


def test_update_code_stages_and_guards(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm
    from odoo_vite.core import venv_manager

    base = tmp_path / "i"
    (base / "community").mkdir(parents=True)
    (base / "community" / ".git").mkdir()
    (base / "community" / "requirements.txt").write_text("six\n")
    (base / "venv" / "bin").mkdir(parents=True)
    inst = _inst(base)
    assert create_instance(inst, db).ok

    # refused while running
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: 1)
    res = mm.update_code(inst, ["sale"])
    assert not res.ok and "Stop" in res.message

    # stages execute in order with labels
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: None)
    calls = []
    import odoo_vite.core.proc as proc

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0,
              cwd=None, env=None, stdin_text=None):
        calls.append(cmd)
        if progress_cb:
            progress_cb("$ " + " ".join(cmd[:3]))
        return Result.success(data={"lines": [], "returncode": 0,
                                    "cancelled": False})

    monkeypatch.setattr(proc, "run_streaming", _fake)
    monkeypatch.setattr(venv_manager, "install_requirements",
                        lambda *a, **k: calls.append(["pip"]) or Result.success())
    logs = []
    res = mm.update_code(inst, ["sale"], progress_cb=logs.append)
    assert res.ok, res.message
    flat = " ".join(" ".join(c) for c in calls)
    assert "pull" in flat and "pip" in flat and "-u" in flat
    assert any("[1/3]" in ln for ln in logs)
    assert any("[3/3]" in ln for ln in logs)
    assert any("Enterprise" in ln for ln in logs)


def test_uninstall_uses_shell_marker(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    calls = []
    import odoo_vite.core.proc as proc

    def _fake(cmd, progress_cb=None, cancel=None, timeout=0,
              cwd=None, env=None, stdin_text=None):
        calls.append((cmd, stdin_text))
        if progress_cb:
            progress_cb("UNINSTALLED: sale")
        return Result.success(data={"lines": ["UNINSTALLED: sale"],
                                    "returncode": 0, "cancelled": False})

    monkeypatch.setattr(proc, "run_streaming", _fake)
    monkeypatch.setattr("odoo_vite.core.process_manager._alive_pid",
                        lambda inst: None)
    base = tmp_path / "i"
    _fs(base)
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.uninstall_modules(inst, "m_db", ["sale"])
    assert res.ok, res.message
    cmd, stdin_text = calls[0]
    assert "shell" in cmd
    assert "button_immediate_uninstall" in stdin_text


def test_diff_statuses(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch, rows=[
        {"name": "sale", "state": "installed", "installed_version": "17.0.1",
         "available_version": "17.0.1", "summary": ""},
        {"name": "ghost", "state": "installed", "installed_version": "17.0.1",
         "available_version": "17.0.1", "summary": ""},
    ])
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    base = tmp_path / "i"
    addons = base / "community" / "addons"
    (addons / "sale").mkdir(parents=True)
    (addons / "sale" / "__manifest__.py").write_text(
        "{'name': 'sale', 'version': '17.0.2'}")
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.diff_modules(inst, "m_db", db_path=db)
    assert res.ok
    by_name = {r["name"]: r["status"] for r in res.data["diff"]}
    assert by_name == {"sale": "disk-newer", "ghost": "manifest-missing"}


def test_diff_adapts_short_framework_versions(tmp_path, db, monkeypatch):
    """Odoo framework manifests say '1.3' meaning '<series>.1.3' (adapt_version)."""
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch, rows=[
        {"name": "base", "state": "installed", "installed_version": "17.0.1.3",
         "available_version": "", "summary": ""},
    ])
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    base = tmp_path / "i"
    addons = base / "community" / "odoo" / "addons"
    (addons / "base").mkdir(parents=True)
    (addons / "base" / "__manifest__.py").write_text("{'name': 'base', 'version': '1.3'}")
    inst = _inst(base, version="17.0")
    assert create_instance(inst, db).ok
    res = mm.diff_modules(inst, "m_db", db_path=db)
    assert res.ok
    assert res.data["diff"][0]["status"] == "in-sync"


def test_diff_no_version_key_is_unknown_not_missing(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch, rows=[
        {"name": "noversion", "state": "installed", "installed_version": "17.0.1",
         "available_version": "", "summary": ""},
    ])
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    base = tmp_path / "i"
    addons = base / "community" / "addons"
    (addons / "noversion").mkdir(parents=True)
    (addons / "noversion" / "__manifest__.py").write_text(
        "{'name': 'No Version'}")
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.diff_modules(inst, "m_db", db_path=db)
    assert res.ok
    assert res.data["diff"][0]["status"] == "unknown"


def test_dependency_graph(tmp_path, db, monkeypatch):
    from odoo_vite.core import module_manager as mm

    _pg_ok(monkeypatch)
    import odoo_vite.core.db_state as st

    monkeypatch.setattr(st, "get_db_state",
                        lambda *a, **k: st.DbState(
                            db_name="m_db", exists=True, initialized=True))
    def _pg_both(instance, db_name, sql, timeout=60):
        if "ir_module_module_dependency" in sql:
            return (0, '{"module": "sale", "depends_on": "base"}\n'
                       '{"module": "sale", "depends_on": "product"}')
        payload = "\n".join(json.dumps(r) for r in ROWS)
        return (0, payload)

    monkeypatch.setattr(mm, "_pg", _pg_both)
    base = tmp_path / "i"
    inst = _inst(base)
    assert create_instance(inst, db).ok
    res = mm.get_dependency_graph(inst, "m_db", db_path=db)
    assert res.ok
    assert len(res.data["edges"]) == 2
    assert {"sale", "base", "product"} <= {n["name"] for n in res.data["nodes"]}


# ------------------------------------------------------- scaffolder
def test_scaffold_generates_installable_shape(tmp_path):
    from odoo_vite.core import module_scaffolder as sc

    definition = {
        "technical_name": "my_library",
        "pretty_name": "My Library",
        "odoo_version": "17.0",
        "models": [
            {"name": "library.book", "description": "Book",
             "fields": [
                 {"name": "name", "type": "char", "required": True},
                 {"name": "pages", "type": "integer"},
                 {"name": "author_id", "type": "many2one",
                  "relation": "res.partner"},
                 {"name": "state", "type": "selection",
                  "selection": [["draft", "Draft"], ["done", "Done"]]},
             ]},
        ],
        "views": True, "menus": True, "security": True,
        "controllers": True, "tests": True, "demo": True,
    }
    res = sc.scaffold(definition, tmp_path)
    assert res.ok, res.message
    root = tmp_path / "my_library"
    for rel in ("__manifest__.py", "__init__.py", "models/__init__.py",
                "models/book.py", "security/ir.model.access.csv",
                "views/book_views.xml", "controllers/main.py",
                "tests/test_basic.py", "demo/demo.xml"):
        assert (root / rel).is_file(), rel
    manifest = (root / "__manifest__.py").read_text()
    assert "'version': '17.0.1.0.0'" in manifest
    assert "'depends': ['base']" in manifest
    models_py = (root / "models" / "book.py").read_text()
    assert "class LibraryBook(models.Model)" in models_py
    assert "author_id = fields.Many2one" in models_py
    # refuses to overwrite
    again = sc.scaffold(definition, tmp_path)
    assert not again.ok and "Refusing to overwrite" in again.message


def test_scaffold_rejects_garbage():
    from odoo_vite.core import module_scaffolder as sc

    assert not sc.scaffold({"technical_name": "Bad-Name!"}, "/tmp").ok
    assert not sc.scaffold({"technical_name": "ok_name",
                            "models": [{"name": "no-dot"}]}, "/tmp").ok
    assert not sc.scaffold({"technical_name": "ok2",
                            "models": [{"name": "a.b",
                                        "fields": [{"name": "x",
                                                    "type": "quantum"}]}]},
                           "/tmp").ok
    assert not sc.scaffold("not-a-dict", "/tmp").ok
