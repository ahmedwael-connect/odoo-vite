"""MP-1 marketplace ops: async drivers + sink routing (no network).

Core functions are monkeypatched — this layer only threads work off the
caller and routes messages/refreshes.
"""

import asyncio

from odoo_vite.core.result import Result
from odoo_vite.ops.marketplace import MarketplaceOps


def _ops():
    messages, refreshes = [], []
    ops = MarketplaceOps(on_message=lambda m, k="info": messages.append((m, k)),
                         on_refresh=lambda: refreshes.append(1))
    return ops, messages, refreshes


def test_search_is_silent(monkeypatch):
    from odoo_vite.core import marketplace as m

    monkeypatch.setattr(m, "search", lambda *a, **k: Result.success(
        data={"items": [], "total": 0, "page": 1, "offline": False,
              "note": "0"}, message="0 apps"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.search("kanban", "Ratings"))
    assert res.ok
    assert messages == []      # silent reads never toast
    assert refreshes == []


def test_detail_and_stats_silent(monkeypatch):
    from odoo_vite.core import marketplace as m

    monkeypatch.setattr(m, "detail", lambda *_a, **_k: Result.success(
        data={"id": "17.0/x"}, message="X"))
    monkeypatch.setattr(m, "stats", lambda **_k: Result.success(
        data={"site_total": 1}, message="1"))
    ops, messages, _ = _ops()
    assert asyncio.run(ops.detail("17.0/x")).ok
    assert asyncio.run(ops.stats()).ok
    assert messages == []


def test_index_toasts_and_refreshes(monkeypatch):
    from odoo_vite.core import marketplace as m

    seen = {}

    def fake_index(owner_repo, branch="", progress_cb=None, cancel=None,
                   db_path=None):
        seen["owner_repo"] = owner_repo
        if progress_cb:
            progress_cb("Cloning …")
        return Result.success(data={"modules": ["mod"]},
                              message="Indexed 1 module(s)")

    monkeypatch.setattr(m, "index", fake_index)
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.index("OCA/demo", progress_cb=lambda _l: None))
    assert res.ok
    assert seen["owner_repo"] == "OCA/demo"
    assert messages == [("Indexed 1 module(s)", "info")]
    assert refreshes == [1]


def test_mutation_failure_toasts_error(monkeypatch):
    from odoo_vite.core import marketplace as m

    monkeypatch.setattr(
        m, "add_review",
        lambda *_a, **_k: Result.failure("Rating must be between 1 and 5"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.add_review("17.0/x", 9, "t", "b", "a"))
    assert not res.ok
    assert messages == [("Rating must be between 1 and 5", "error")]
    assert refreshes == [1]  # _run always refreshes (idempotent)


def test_install_from_zip_refreshes(monkeypatch):
    from odoo_vite.core import marketplace as m

    monkeypatch.setattr(
        m, "import_addon_zip",
        lambda *_a, **_k: Result.success(data={"modules": ["mod"]},
                                         message="Added mod"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.install_from_zip("/tmp/x.zip", "inst-1"))
    assert res.ok
    assert messages[0][0] == "Added mod"
    assert refreshes == [1]


def test_record_download_is_sync_answer(monkeypatch):
    from odoo_vite.core import marketplace as m

    calls = []
    monkeypatch.setattr(m, "record_download", lambda mid: calls.append(mid))
    ops, _, _ = _ops()
    res = ops.record_download("17.0/x")
    assert res == {"ok": True, "message": "counted"}
    assert calls == ["17.0/x"]


def test_install_from_zip_records_marketplace_install(monkeypatch):
    from odoo_vite.core import marketplace as m

    calls = {}

    def fake_import(zip_path, instance_id, db_path=None):
        return Result.success(data={"modules": ["mod_a", "mod_b"],
                                    "path": "/x/addons"},
                              message="Added mod_a, mod_b to Demo")

    monkeypatch.setattr(m, "import_addon_zip", fake_import)
    monkeypatch.setattr(m, "get_module",
                        lambda mid, db_path=None: {"series": "17.0"})
    monkeypatch.setattr(
        m, "record_install",
        lambda mid, inst, tech, ver="", method="zip", db_path=None: (
            calls.setdefault("rows", []).append(
                (mid, inst, tech, ver, method)),
            Result.success())[1])

    ops, messages, refreshes = _ops()
    res = asyncio.run(
        ops.install_from_zip("/tmp/a.zip", "inst-1", "17.0/mod_a"))
    assert res.ok
    assert calls["rows"] == [
        ("17.0/mod_a", "inst-1", "mod_a", "17.0", "zip"),
        ("17.0/mod_a", "inst-1", "mod_b", "17.0", "zip"),
    ]
    assert messages and messages[0][0].startswith("Added mod_a")
    assert refreshes == [1, 1]  # _run refresh + badges refresh


def test_install_from_zip_without_module_id_skips_recording(monkeypatch):
    from odoo_vite.core import marketplace as m

    monkeypatch.setattr(
        m, "import_addon_zip",
        lambda *_a, **_k: Result.success(data={"modules": ["x"]},
                                         message="Added x"))
    monkeypatch.setattr(
        m, "record_install",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no record")))
    ops, _, refreshes = _ops()
    res = asyncio.run(ops.install_from_zip("/tmp/a.zip", "inst-1"))
    assert res.ok
    assert refreshes == [1]


def test_open_url_scheme_gate(monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open",
                        lambda u: opened.append(u) or True)
    ops, messages, _ = _ops()

    ok = asyncio.run(ops.open_url("https://apps.odoo.com/apps/17.0/x"))
    assert ok.ok and opened == ["https://apps.odoo.com/apps/17.0/x"]

    bad = asyncio.run(ops.open_url("javascript:alert(1)"))
    assert not bad.ok and "non-http" in bad.message
    file_url = asyncio.run(ops.open_url("file:///etc/passwd"))
    assert not file_url.ok
    assert opened == ["https://apps.odoo.com/apps/17.0/x"]  # untouched


def test_open_url_no_browser(monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: False)
    ops, _, _ = _ops()
    res = asyncio.run(ops.open_url("https://example.com"))
    assert not res.ok and "No browser" in res.message


def test_watch_download_success_counts_store_download(monkeypatch):
    from odoo_vite.core import marketplace as m

    counted = []
    monkeypatch.setattr(
        m, "watch_download",
        lambda tech, timeout=600.0, progress_cb=None, cancel=None,
        db_path=None: (
            progress_cb and progress_cb("Watching …"),
            Result.success(data={"path": "/d/a.zip", "size": 10},
                           message="Downloaded a.zip"))[1])
    monkeypatch.setattr(m, "record_download", lambda mid: counted.append(mid))
    ops, _, _ = _ops()
    seen = []
    res = asyncio.run(
        ops.watch_download("17.0/x", "mod_a",
                           progress_cb=seen.append))
    assert res.ok and res.data["path"] == "/d/a.zip"
    assert counted == ["17.0/x"]
    assert seen and "Watching" in seen[0]
