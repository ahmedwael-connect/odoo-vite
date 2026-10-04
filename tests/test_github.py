"""3.3.0 Feature B: GitHub core — token, auth env, install/sync/publish.

All git operations run against local repos (no network, no real GitHub);
token/HTTP paths are monkeypatched.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from odoo_vite.core import github
from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, get_instance, init_db

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not installed"
)

_COMMIT = ("-c", "user.name=OdooVite", "-c", "user.email=odoo-vite@test",
           "-c", "commit.gpgsign=false")
_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(cwd, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=60, env=_ENV,
    )
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}"
    )
    return proc.stdout


def _fake_keyring(monkeypatch) -> dict:
    store: dict[str, str] = {}
    monkeypatch.setattr(github, "_keyring_set",
                        lambda t: store.__setitem__("token", t))
    monkeypatch.setattr(github, "_keyring_get",
                        lambda: store.get("token", ""))
    monkeypatch.setattr(github, "_keyring_del",
                        lambda: store.pop("token", None))
    return store


def _repo_with_module(tmp_path, name="repo1", manifest_name="Mod A"):
    """Local git repo, branch main: <name>/mod_a/__manifest__.py."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    mod = repo / "mod_a"
    mod.mkdir()
    (mod / "__manifest__.py").write_text(
        f"{{'name': '{manifest_name}', 'version': '17.0.1.0.0'}}\n")
    (mod / "__init__.py").write_text("")
    _git(repo, "add", "-A")
    _git(repo, *_COMMIT, "commit", "-m", "init")
    return repo


def _bare(tmp_path, name="origin.git"):
    origin = tmp_path / name
    origin.mkdir()
    _git(origin, "init", "--bare")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    return origin


def _fake_instance(tmp_path, db, name="GhI"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    conf = root / "odoo.conf"
    (root / "core_addons").mkdir(exist_ok=True)
    conf.write_text("[options]\naddons_path = %s\n" % (root / "core_addons"))
    inst = Instance(name=name, version="17.0", mode="managed",
                    path=str(root), conf_path=str(conf))
    assert create_instance(inst, db).ok
    return inst


# ------------------------------------------------------------------ token


def test_token_flow_validates_then_keyring(monkeypatch):
    store = _fake_keyring(monkeypatch)
    seen = {}

    def fake_validate(token):
        seen["token"] = token
        if token == "good":
            return github.Result.success(data={"login": "octo"},
                                         message="valid")
        return github.Result.failure("bad token")

    monkeypatch.setattr(github, "validate_token", fake_validate)

    res = github.save_token("bad")
    assert not res.ok and "token" not in store

    res = github.save_token("good")
    assert res.ok and res.data["login"] == "octo"
    assert store["token"] == "good"
    assert github.token_status() == {"saved": True}
    assert github.get_token() == "good"
    # the token itself never appears in the success message
    assert "good" not in res.message

    assert github.clear_token().ok
    assert github.token_status() == {"saved": False}


def test_git_auth_env_scopes(monkeypatch):
    store = _fake_keyring(monkeypatch)

    # E2: SSH remotes never see the token
    assert github.git_auth_env("git@github.com:odoo/odoo") == {}
    assert github.git_auth_env("ssh://git@github.com/odoo/odoo") == {}
    # plain http never gets the token
    assert github.git_auth_env("http://github.com/odoo/odoo") == {}
    # no token saved → nothing
    assert github.git_auth_env("https://github.com/odoo/odoo") == {}
    assert github.git_auth_env("") == {}

    store["token"] = "ghp_secret"
    env = github.git_auth_env("https://github.com/odoo/odoo")
    assert env["ODOO_VITE_GH_TOKEN"] == "ghp_secret"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    from pathlib import Path

    script = Path(env["GIT_ASKPASS"])
    assert script.is_file() and os.access(script, os.X_OK)
    body = script.read_text()
    assert "x-access-token" in body and "ghp_secret" not in body

    # token still refused on plaintext http
    assert github.git_auth_env("http://github.com/x/y") == {}


# ------------------------------------------------------------------ urls


def test_repo_url_and_name():
    res = github._repo_url("OCA/web-responsive")
    assert res.ok and res.data == "https://github.com/OCA/web-responsive"
    res = github._repo_url("https://github.com/odoo/odoo.git")
    assert res.ok and res.data == "https://github.com/odoo/odoo.git"
    assert not github._repo_url("").ok
    assert not github._repo_url("not a repo!!").ok
    assert github._repo_name("OCA/web_modules") == "web_modules"
    assert github._repo_name("https://github.com/a/My.Repo.git") == "my_repo"


def test_list_repo_branches_local(tmp_path):
    repo = _repo_with_module(tmp_path)
    _git(repo, "branch", "dev")
    github.clear_branch_cache()

    res = github.list_repo_branches(str(repo))
    assert res.ok, res.message
    assert set(res.data) == {"main", "dev"}

    # cached second call
    res2 = github.list_repo_branches(str(repo))
    assert res2.ok and "cached" in res2.message
    github.clear_branch_cache()

    # owner/repo-shaped garbage never reaches git
    assert not github.list_repo_branches("").ok


# ---------------------------------------------------------------- install


def test_install_repo_single_module(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    repo = _repo_with_module(tmp_path, "GhRepo")

    res = asyncio.run(github.install_repo(inst.id, str(repo), "main",
                                          db_path=db))
    assert res.ok, res.message
    # repo layout: modules sit one level down → register the repo folder
    assert res.data["modules"] == ["mod_a"]
    dest = tmp_path / inst.name / "addons" / "ghrepo"
    assert (dest / "mod_a" / "__manifest__.py").is_file()

    from pathlib import Path

    stored = get_instance(inst.id, db)
    paths = [e["path"] for e in (stored.addons_state or [])]
    assert str(dest) in paths
    assert str(dest) in Path(inst.conf_path).read_text()

    # collision: no overwrite
    res = asyncio.run(github.install_repo(inst.id, str(repo), "main",
                                          db_path=db))
    assert not res.ok and "already exists" in res.message


def test_install_repo_repo_style_layout(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    repo = tmp_path / "multi"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    mod = repo / "addons" / "mod_b"
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'B'}\n")
    _git(repo, "add", "-A")
    _git(repo, *_COMMIT, "commit", "-m", "init")

    res = asyncio.run(github.install_repo(inst.id, str(repo), "main",
                                          db_path=db))
    assert res.ok, res.message
    assert res.data["modules"] == ["mod_b"]
    stored = get_instance(inst.id, db)
    paths = [e["path"] for e in (stored.addons_state or [])]
    assert str(tmp_path / inst.name / "addons" / "multi" / "addons") in paths


def test_install_repo_no_manifest_removes_clone(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    repo = tmp_path / "emptyrepo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, *_COMMIT, "commit", "-m", "init")

    res = asyncio.run(github.install_repo(inst.id, str(repo), "main",
                                          db_path=db))
    assert not res.ok and "no Odoo module" in res.message
    assert not (tmp_path / inst.name / "addons" / "emptyrepo").exists()


def test_install_repo_validation(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)

    assert not asyncio.run(github.install_repo("nope", "a/b", "main",
                                               db_path=db)).ok
    assert not asyncio.run(github.install_repo(inst.id, "!!bad!!", "main",
                                               db_path=db)).ok
    assert not asyncio.run(github.install_repo(inst.id, "a/b", "",
                                               db_path=db)).ok
    assert asyncio.run(github.install_repo(
        inst.id, "a/b", "", cancel=lambda: True, db_path=db)).message \
        == "Cancelled"


# ------------------------------------------------------------------- sync


def test_sync_module_fast_forwards(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    origin = _bare(tmp_path)

    src = tmp_path / "src"
    _git(tmp_path, "clone", str(origin), str(src))
    _git(src, "checkout", "-B", "main")
    mod = src / "mod_a"
    mod.mkdir()
    (mod / "__manifest__.py").write_text("{'name': 'A'}\n")
    _git(src, "add", "-A")
    _git(src, *_COMMIT, "commit", "-m", "one")
    _git(src, "push", "-u", "origin", "main")

    addons = tmp_path / inst.name / "addons"
    _git(tmp_path, "clone", str(origin), str(addons))

    # advance the remote from src
    (mod / "extra.py").write_text("x = 1\n")
    _git(src, "add", "-A")
    _git(src, *_COMMIT, "commit", "-m", "two")
    _git(src, "push", "origin", "main")

    res = asyncio.run(github.sync_module(inst.id, "mod_a", db_path=db))
    assert res.ok, res.message
    assert (addons / "mod_a" / "extra.py").is_file()
    assert "mod_a" in res.message

    # validation
    assert not asyncio.run(github.sync_module(inst.id, "Bad-Name",
                                              db_path=db)).ok
    assert not asyncio.run(github.sync_module(inst.id, "nope",
                                              db_path=db)).ok
    assert not asyncio.run(github.sync_module("missing", "mod_a",
                                              db_path=db)).ok


def test_sync_module_non_git(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    addons = tmp_path / inst.name / "addons"
    mod = addons / "mod_a"
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'A'}\n")

    res = asyncio.run(github.sync_module(inst.id, "mod_a", db_path=db))
    assert not res.ok and "not inside a git checkout" in res.message


# ---------------------------------------------------------------- publish


def test_publish_state(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)

    state = github.publish_state(inst.id, db)
    assert state["error"] == ""
    assert state["folder"].endswith("/addons")
    assert state["in_git"] is False

    # inside a repo → remote + branch detected
    addons = tmp_path / inst.name / "addons"
    addons.mkdir(exist_ok=True)
    origin = _bare(tmp_path)
    _git(tmp_path, "clone", str(origin), str(addons))
    _git(addons, "checkout", "-B", "main")
    state = github.publish_state(inst.id, db)
    assert state["in_git"] is True
    assert state["branch"] == "main"
    assert state["remote"].endswith("origin.git")

    assert github.publish_state("missing", db)["error"]


def test_publish_refuses_secrets(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    origin = _bare(tmp_path)
    src = tmp_path / "src"
    _git(tmp_path, "clone", str(origin), str(src))
    _git(src, "checkout", "-B", "main")
    (src / "mod_a").mkdir()
    (src / "mod_a" / "__manifest__.py").write_text("{'name': 'A'}\n")
    _git(src, "add", "-A")
    _git(src, *_COMMIT, "commit", "-m", "one")
    _git(src, "push", "-u", "origin", "main")

    addons = tmp_path / inst.name / "addons"
    _git(tmp_path, "clone", str(origin), str(addons))
    # user drops a secret + a normal file
    (addons / ".env").write_text("SECRET=1\n")
    (addons / "notes.txt").write_text("hello\n")

    res = asyncio.run(github.publish_addons(
        inst.id, "add notes", db_path=db))
    assert not res.ok
    assert "Refusing to publish" in res.message
    assert ".env" in res.message
    # nothing was pushed
    out = _git(origin, "log", "-1", "--format=%s", "main")
    assert out.strip() == "one"


def test_publish_existing_repo_pushes(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    origin = _bare(tmp_path)
    src = tmp_path / "src"
    _git(tmp_path, "clone", str(origin), str(src))
    _git(src, "checkout", "-B", "main")
    (src / "mod_a").mkdir()
    (src / "mod_a" / "__manifest__.py").write_text("{'name': 'A'}\n")
    _git(src, "add", "-A")
    _git(src, *_COMMIT, "commit", "-m", "one")
    _git(src, "push", "-u", "origin", "main")

    addons = tmp_path / inst.name / "addons"
    _git(tmp_path, "clone", str(origin), str(addons))
    (addons / "notes.txt").write_text("hello\n")

    res = asyncio.run(github.publish_addons(
        inst.id, "add notes file", db_path=db))
    assert res.ok, res.message
    assert res.data["rev"]
    assert _git(origin, "log", "-1", "--format=%s", "main").strip() \
        == "add notes file"
    assert (tmp_path / "origin.git" / "notes.txt").is_file() or \
        "notes" in _git(origin, "ls-tree", "-r", "--name-only", "main")

    # nothing new to commit → still pushes, reports the fact
    res = asyncio.run(github.publish_addons(
        inst.id, "empty again", db_path=db))
    assert res.ok and "nothing new to commit" in res.message


def test_publish_create_repo(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)
    origin = _bare(tmp_path)  # remote path — exists, so no API needed
    addons = tmp_path / inst.name / "addons"
    addons.mkdir(parents=True)
    (addons / "mod_a").mkdir()
    (addons / "mod_a" / "__manifest__.py").write_text("{'name': 'A'}\n")

    # create=False and no git → refuse with guidance
    res = asyncio.run(github.publish_addons(
        inst.id, "first push", db_path=db))
    assert not res.ok and "not a git repository" in res.message

    res = asyncio.run(github.publish_addons(
        inst.id, "first push", branch="main", remote=str(origin),
        create=True, db_path=db))
    assert res.ok, res.message
    assert (addons / ".git").is_dir()
    refs = _git(origin, "for-each-ref", "--format=%(refname)")
    assert "refs/heads/" in refs

    # local-path remote: never touched by the github API create path
    assert not github._is_github_https(str(origin))


def test_publish_validation(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    inst = _fake_instance(tmp_path, db)

    assert not asyncio.run(github.publish_addons(inst.id, "",
                                                 db_path=db)).ok
    assert not asyncio.run(github.publish_addons("missing", "x",
                                                 db_path=db)).ok
    assert asyncio.run(github.publish_addons(
        inst.id, "x", cancel=lambda: True, db_path=db)).message \
        == "Cancelled"


def test_secret_pattern_matrix():
    for bad in (".env", ".env.local", "cfg/app.PEM", "mods/id_rsa",
                "a/b/id_ed25519", "x.key", "net/.netrc"):
        assert github._looks_secret(bad), bad
    for good in ("README.md", "mod_a/__manifest__.py",
                 "static/description/icon.png", "skeleton.py",
                 "wizards/manifest.py"):
        assert not github._looks_secret(good), good


# ------------------------------------------------------------------ E1


def test_marketplace_request_headers_scoped(monkeypatch):
    from odoo_vite.core import marketplace

    monkeypatch.setattr(github, "get_token", lambda: "tok123")
    h = marketplace._request_headers("https://api.github.com/repos/x/y")
    assert h["Authorization"] == "Bearer tok123"
    # token never leaves api.github.com
    for other in ("https://apps.odoo.com/api/search",
                  "https://github.com/odoo/odoo",
                  "https://example.com/"):
        assert "Authorization" not in marketplace._request_headers(other)

    monkeypatch.setattr(github, "get_token", lambda: "")
    assert "Authorization" not in marketplace._request_headers(
        "https://api.github.com/x")


# ------------------------------------------------------------------- api


def test_api_exposes_github_domain(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "app.db"))
    from odoo_vite.ui_web.api import create_api

    api, _push = create_api()
    assert api.github.token_status() == {"saved": False}
    assert not api.github.save_token("")["ok"]
    state = api.github.publish_state("missing-id")
    assert state["error"]
    assert not api.github.publish("missing-id", "msg")["ok"]
