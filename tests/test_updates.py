"""3.3.0 Feature A: app-wide pull (updates). Fully local — bare repos only."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from odoo_vite.core.instance import Instance
from odoo_vite.core.registry import create_instance, init_db
from odoo_vite.core.updates import collect_checkouts, pull_all, pull_path

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


def _make_remote(tmp_path):
    """bare origin + seed (1 commit) + work clone tracking origin/main."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(origin), str(seed))
    _git(seed, "checkout", "-B", "main")
    (seed / "README.md").write_text("one\n")
    _git(seed, "add", "README.md")
    _git(seed, *_COMMIT, "commit", "-m", "one")
    _git(seed, "push", "-u", "origin", "main")
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    return origin, seed, work


def _advance(repo, text: str, msg: str) -> None:
    (repo / "README.md").write_text(text)
    _git(repo, "add", "README.md")
    _git(repo, *_COMMIT, "commit", "-m", msg)
    _git(repo, "push", "origin", "main")


def test_collect_checkouts_dedupes(tmp_path):
    db = tmp_path / "reg.db"
    assert init_db(db).ok
    shared = tmp_path / "odoo"
    shared.mkdir()
    ent = tmp_path / "ent"
    ent.mkdir()
    a = Instance(name="a", path=str(tmp_path / "a"),
                 community_path=str(shared), enterprise_path=str(ent))
    b = Instance(name="b", path=str(tmp_path / "b"),
                 community_path=str(shared))
    c = Instance(name="c", path=str(tmp_path / "c"))
    assert create_instance(a, db).ok
    assert create_instance(b, db).ok
    assert create_instance(c, db).ok

    rows = collect_checkouts(db)
    by_path = {r["path"]: r for r in rows}
    assert len(rows) == 2
    assert by_path[str(shared)]["kind"] == "community"
    assert sorted(by_path[str(shared)]["instances"]) == ["a", "b"]
    assert by_path[str(ent)]["kind"] == "enterprise"


def test_pull_path_rejects_missing_and_non_git(tmp_path):
    assert "does not exist" in pull_path(str(tmp_path / "nope")).message
    plain = tmp_path / "plain"
    plain.mkdir()
    res = pull_path(str(plain))
    assert not res.ok and "not a git checkout" in res.message
    assert "No path" in pull_path("").message


def test_pull_path_skips_dirty(tmp_path):
    _origin, _seed, work = _make_remote(tmp_path)
    (work / "README.md").write_text("local edit\n")
    lines: list[str] = []
    res = pull_path(str(work), progress_cb=lines.append)
    assert not res.ok
    assert "uncommitted change" in res.message
    assert res.data["dirty"] == 1
    assert not any("fetch" in ln for ln in lines), "dirty tree must not fetch"


def test_pull_path_fast_forwards_then_up_to_date(tmp_path):
    _origin, seed, work = _make_remote(tmp_path)
    _advance(seed, "two\n", "two")

    lines: list[str] = []
    res = pull_path(str(work), progress_cb=lines.append)
    assert res.ok, res.message
    assert res.data["updated"] == 1
    assert (work / "README.md").read_text() == "two\n"
    assert any("fast-forward" in ln for ln in lines)

    res2 = pull_path(str(work))
    assert res2.ok and res2.data["updated"] == 0
    assert "already up to date" in res2.message


def test_pull_path_reports_divergence(tmp_path):
    _origin, seed, work = _make_remote(tmp_path)
    _advance(seed, "remote\n", "remote")
    (work / "README.md").write_text("local\n")
    _git(work, "add", "README.md")
    _git(work, *_COMMIT, "commit", "-m", "local")
    res = pull_path(str(work))
    assert not res.ok
    assert "diverged" in res.message


def test_pull_path_cancel(tmp_path):
    _make_remote(tmp_path)
    res = pull_path(str(tmp_path / "work"), cancel=lambda: True)
    assert not res.ok
    assert res.message.startswith("Cancelled")


def test_pull_all_continues_past_failures(tmp_path):
    _origin, seed, work = _make_remote(tmp_path)
    _advance(seed, "two\n", "two")
    plain = tmp_path / "plain"
    plain.mkdir()

    res = asyncio.run(pull_all([str(plain), str(work)]))
    assert not res.ok
    assert "1 of 2" in res.message
    statuses = [r["status"] for r in res.data["results"]]
    assert statuses == ["failed", "ok"]
    assert (work / "README.md").read_text() == "two\n"


def test_pull_all_validation(tmp_path):
    res = asyncio.run(pull_all([]))
    assert not res.ok and "No checkouts selected" in res.message

    res = asyncio.run(pull_all(["x"], cancel=lambda: True))
    assert not res.ok and res.message.startswith("Cancelled")

    _origin, seed, work = _make_remote(tmp_path)
    _advance(seed, "two\n", "two")
    res = asyncio.run(pull_all([str(work)]))
    assert res.ok
    assert [r["status"] for r in res.data["results"]] == ["ok"]


def test_api_exposes_updates_domain(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "app.db"))
    from odoo_vite.ui_web.api import create_api

    api, _push = create_api()
    payload = api.updates.list()
    assert payload == {"checkouts": []}
    bad = api.updates.pull([])
    assert not bad["ok"]
    assert "No checkouts selected" in bad["message"]
