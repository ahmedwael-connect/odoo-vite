"""MP-1 marketplace core: store parsers (fixture-pinned), sanitizer,
storage, cache, GitHub index, zip import — all offline.

Fixtures captured from apps.odoo.com in P0 (tests/fixtures/marketplace/);
the network seam is marketplace._http_get_url (monkeypatched here —
core tests never touch the network).
"""

import subprocess
import zipfile
from pathlib import Path


from odoo_vite.core import marketplace as m
from odoo_vite.core.registry import Instance, create_instance, init_db

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ parsers


def test_parse_cards_browse_default():
    items = m.parse_cards(_fx("browse_default.html"))
    assert len(items) == 20
    first = items[0]
    assert first["id"] == "20.0/shopify_ept"
    assert first["series"] == "20.0"
    assert first["tech"] == "shopify_ept"
    assert first["title"] == "Shopify Odoo Connector"
    assert first["author"] == "Emipro Technologies Pvt. Ltd."
    assert first["price"] == "$624.65"
    assert first["free"] is False
    assert first["rating_count"] == 298
    assert first["purchases"] == 2423
    assert first["cover_url"].startswith("https://apps.odoocdn.com/")
    assert first["source"] == "mirror"


def test_parse_cards_search_and_category():
    kanban = m.parse_cards(_fx("search_kanban.html"))
    assert len(kanban) == 20
    assert all("kanban" in i["id"].lower()
               or "kanban" in i["title"].lower()
               or "kanban" in i["summary"].lower()
               for i in kanban[:5])
    cat = m.parse_cards(_fx("category_extratools.html"))
    assert len(cat) == 20
    assert cat[0]["id"] == "20.0/printnode_base"


def test_parse_total():
    assert m.parse_total(_fx("browse_default.html")) == 93901
    assert m.parse_total(_fx("search_kanban.html")) == 656
    assert m.parse_total(_fx("category_extratools.html")) == 12190
    assert m.parse_total("<html>nothing here</html>") == 0


def test_parse_categories():
    cats = m.parse_categories(_fx("browse_default.html"))
    assert "Accounting" in cats
    assert "Extra Tools" in cats
    assert cats == list(dict.fromkeys(cats))  # deduped, order kept


def test_parse_detail_free_full():
    d = m.parse_detail(_fx("detail_free.html"), "17.0")
    assert d["tech"] == "pos_negative_stock_restrict"
    assert d["id"] == "17.0/pos_negative_stock_restrict"
    assert d["author"] == "Vishnu Sasikumar"
    assert d["free"] is True
    assert d["dl_hash"] == "7c1mtaj20LYXieNxgJ9WvD"
    assert d["dl_version"] == "17.0"
    assert d["versions"] == ["17.0", "18.0", "19.0"]
    dep_techs = [x["tech"] for x in d["depends"]]
    assert dep_techs == ["stock", "point_of_sale", "mail", "account"]
    assert d["depends"][1]["label"] == "Point of Sale"
    assert d["available"] == {
        "odoo_online": False, "odoosh": True, "on_premise": True}
    assert d["license"] == "LGPL-3"
    assert d["purchases"] == 3
    assert d["downloads"] == 104
    assert "<" in d["description_html"]
    assert "script" not in d["description_html"].lower()


def test_parse_detail_rated():
    d = m.parse_detail(_fx("detail_rated.html"), "20.0")
    assert d["tech"] == "mcp_server_odoo"
    assert d["free"] is False
    assert d["dl_hash"] == ""
    assert d["rating_value"] == 5.0
    assert d["rating_count"] == 7
    assert d["review_count"] == 8
    assert len(d["reviews"]) == 1
    review = d["reviews"][0]
    assert review["remote_id"] == "87243"
    assert review["title"].startswith("Can I train it")
    assert review["author"] == "Abdulaziz Alanazi"
    assert review["source"] == "store"


def test_parse_detail_official():
    d = m.parse_detail(_fx("detail_official.html"), "20.0")
    assert d["tech"] == "it_hardware"
    assert d["author"] == "Odoo S.A."
    assert d["free"] is True
    assert d["available"]["odoo_online"] is True
    assert "knowledge" in [x["tech"] for x in d["depends"]]


def test_parse_charts():
    charts = m.parse_charts(_fx("home_charts.html"))
    assert set(charts) == {"Top Apps", "New Apps", "Most Downloaded"}
    assert charts["Top Apps"][0] == "20.0/shopify_ept"
    assert all(len(v) == 4 for v in charts.values())


def test_parse_detail_missing_technical_name():
    d = m.parse_detail("<html><h1>Drift</h1></html>", "17.0")
    assert d["tech"] == ""


# ---------------------------------------------------------------- sanitizer


def test_sanitize_drops_script_and_handlers():
    out = m.sanitize_html(
        '<script>alert(1)</script><p onclick="x()">hi</p>'
        '<a href="javascript:alert(1)">bad</a>'
        '<img src="//apps.odoocdn.com/a.png" onerror="bad">'
        '<img src="http://evil/x.png">')
    assert "<script" not in out
    assert "onclick" not in out
    assert "javascript:" not in out
    assert "onerror" not in out
    assert "https://apps.odoocdn.com/a.png" in out
    assert "http://evil/x.png" not in out
    assert "<p>hi</p>" in out


def test_sanitize_strips_style_class_keeps_semantics():
    out = m.sanitize_html(
        '<div class="st" style="position:fixed">x</div>'
        '<h2>Title</h2><ul><li>a</li></ul>'
        '<table><tr><td colspan="2">c</td></tr></table>')
    assert 'class=' not in out
    assert 'style=' not in out
    assert "<h2>Title</h2>" in out
    assert "<li>a</li>" in out
    assert 'colspan="2"' in out


def test_sanitize_escapes_text_and_keeps_https_link():
    out = m.sanitize_html(
        '<p>Tom &amp; Jerry <b>bold</b></p>'
        '<a href="https://example.com/x" title="t">site</a>')
    assert "Tom &amp; Jerry" in out
    assert "<b>bold</b>" in out
    assert 'href="https://example.com/x"' in out


# ------------------------------------------------------------------ storage


def _db(tmp_path):
    db = tmp_path / "marketplace.db"
    assert init_db(db).ok
    return db


def test_upsert_partial_preserves_detail(tmp_path):
    db = _db(tmp_path)
    detail_row = {
        "id": "17.0/foo", "tech_name": "foo", "series": "17.0",
        "title": "Foo", "summary": "S", "author": "A",
        "source": "mirror", "free": True, "description_html": "<p>LONG</p>",
        "versions_json": '["17.0"]', "rating_value": 4.5,
        "depends_json": '[{"label": "Base", "tech": "base"}]',
    }
    assert m.upsert_modules([detail_row], db).ok
    # a bare search hit for the same module must not wipe detail data
    search_row = {"id": "17.0/foo", "tech_name": "foo", "series": "17.0",
                  "title": "Foo", "summary": "S2", "source": "mirror",
                  "free": True, "rating_count": 12}
    assert m.upsert_modules([search_row], db).ok
    row = m.get_module("17.0/foo", db)
    assert row["description_html"] == "<p>LONG</p>"
    assert row["rating_value"] == 4.5
    assert row["versions"] == ["17.0"]
    assert row["depends"][0]["tech"] == "base"
    assert row["summary"] == "S2"
    assert row["rating_count"] == 12


def test_search_local_matching_and_source_filter(tmp_path):
    db = _db(tmp_path)
    assert m.upsert_modules([
        {"id": "17.0/kanban_guy", "tech_name": "kanban_guy",
         "title": "Kanban Guy", "summary": "boards", "author": "Zed",
         "source": "mirror"},
        {"id": "gh:o/r:other", "tech_name": "other",
         "title": "Something", "summary": "", "author": "Zed",
         "source": "github"},
    ], db).ok
    hits = m.search_local("KANBAN", db_path=db)
    assert [h["id"] for h in hits] == ["17.0/kanban_guy"]
    gh = m.search_local(source="github", db_path=db)
    assert [h["id"] for h in gh] == ["gh:o/r:other"]
    by_author = m.search_local("zed", db_path=db)
    assert len(by_author) == 2


def test_flags_and_stats(tmp_path):
    db = _db(tmp_path)
    assert m.upsert_modules([{
        "id": "17.0/a", "tech_name": "a", "title": "A", "source": "mirror",
        "featured": False, "official": False,
    }], db).ok
    assert m.set_flags("17.0/a", featured=True, db_path=db).ok
    assert m.get_module("17.0/a", db)["featured"] is True
    assert not m.set_flags("17.0/ghost", featured=True, db_path=db).ok
    assert not m.set_flags("17.0/a", db_path=db).ok

    assert m.add_review("17.0/a", 4, "Nice", "works well", "me", db).ok
    assert m.add_review("17.0/a", 5, "Great", "", "me", db).ok
    stats = m.db_stats(db)
    assert stats["cached"] == 1
    assert stats["reviews"] == 2
    assert stats["avg_local_rating"] == 4.5
    assert stats["featured"] == 1

    assert m.record_install("17.0/a", "inst-1", "a", "17.0", db_path=db).ok
    assert m.db_stats(db)["installs"] == 1
    assert m.installed_in("inst-1", db)[0]["tech_name"] == "a"
    assert m.installed_in("other", db) == []


def test_review_validation(tmp_path):
    db = _db(tmp_path)
    assert not m.add_review("x", 0, "t", "b", "", db).ok
    assert not m.add_review("x", 6, "t", "b", "", db).ok
    assert not m.add_review("x", 3, "", "", "", db).ok
    assert not m.add_review("x", 3, "t" * 201, "", "", db).ok
    assert m.add_review("x", 3, "ok", "body", "a", db).ok
    reviews = m.list_reviews("x", db)
    assert len(reviews) == 1
    assert m.delete_review(reviews[0]["id"], db).ok
    assert not m.delete_review(reviews[0]["id"], db).ok
    assert m.list_reviews("x", db) == []


def test_record_download_bumps_counter(tmp_path):
    db = _db(tmp_path)
    assert m.upsert_modules([{"id": "17.0/a", "tech_name": "a",
                              "source": "mirror", "downloads": 5}],
                            db).ok
    m.record_download("17.0/a", db)
    m.record_download("17.0/a", db)
    assert m.get_module("17.0/a", db)["downloads"] == 7


# -------------------------------------------------------------------- cache


def test_http_get_cache_and_stale(tmp_path, monkeypatch):
    db = _db(tmp_path)
    calls = []

    def fake(url):
        calls.append(url)
        return b"payload-1"

    monkeypatch.setattr(m, "_http_get_url", fake)
    res = m.http_get("/apps/modules/browse", ttl=999, db_path=db)
    assert res.ok and res.data == "payload-1" and len(calls) == 1

    res = m.http_get("/apps/modules/browse", ttl=999, db_path=db)
    assert res.ok and res.data == "payload-1"
    assert len(calls) == 1  # fresh → cache

    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(m, "_http_get_url", boom)
    res = m.http_get("/apps/modules/browse", ttl=0, db_path=db)
    assert res.ok and res.data == "payload-1"
    assert "no network" in res.message and "cached" in res.message

    # never fetched, network down → failure
    res = m.http_get("/other", ttl=999, db_path=db)
    assert not res.ok and "no network" in res.message


def _fixture_fetcher(calls):
    def fake(url):
        calls.append(url)
        if "/category/" in url:
            return (FIXTURES / "category_extratools.html").read_bytes()
        if "search=kanban" in url:
            return (FIXTURES / "search_kanban.html").read_bytes()
        if "search=IT+Hardware" in url or "search=IT%20Hardware" in url:
            return (FIXTURES / "search_official.html").read_bytes()
        if "/17.0/pos_negative_stock_restrict" in url:
            return (FIXTURES / "detail_free.html").read_bytes()
        if "/20.0/mcp_server_odoo" in url:
            return (FIXTURES / "detail_rated.html").read_bytes()
        if "/20.0/it_hardware" in url:
            return (FIXTURES / "detail_official.html").read_bytes()
        if url.rstrip("/").endswith("apps.odoo.com") or url.endswith("/"):
            return (FIXTURES / "home_charts.html").read_bytes()
        if "/apps/modules/browse" in url:
            return (FIXTURES / "browse_default.html").read_bytes()
        return b"<html><h1>Unknown fixture URL</h1></html>"
    return fake


def test_fetch_search_and_detail_via_seam(tmp_path, monkeypatch):
    db = _db(tmp_path)
    calls = []
    monkeypatch.setattr(m, "_http_get_url", _fixture_fetcher(calls))

    res = m.fetch_search("kanban", db_path=db)
    assert res.ok
    assert res.data["total"] == 656
    assert len(res.data["items"]) == 20

    res = m.fetch_search(order="Ratings", category="Extra Tools", db_path=db)
    assert res.ok
    assert any("order=Ratings" in u for u in calls)
    assert any("category/Extra%20Tools" in u for u in calls)

    assert not m.fetch_search(order="Bogus", db_path=db).ok
    assert not m.fetch_search(price="Sorta", db_path=db).ok

    res = m.fetch_detail("17.0", "pos_negative_stock_restrict", db_path=db)
    assert res.ok and res.data["free"] is True

    assert not m.fetch_detail("17.0", "does_not_exist", db_path=db).ok
    assert not m.fetch_detail("", "", db_path=db).ok


def test_search_online_then_offline(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(m, "_http_get_url", _fixture_fetcher([]))

    res = m.search("kanban", db_path=db)
    assert res.ok
    assert res.data["offline"] is False
    assert res.data["total"] == 656
    # rows were cached for offline use
    assert m.get_module("20.0/sh_access_management", db) is not None

    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(m, "_http_get_url", boom)
    res = m.search("Softhealer", db_path=db)
    assert res.ok and res.data["offline"] is True
    assert any(i["tech"] == "sh_access_management"
               for i in res.data["items"])

    # detail falls back to cache only when a rich row exists
    res = m.search("", db_path=db)
    assert res.ok and res.data["offline"] is True
    assert len(res.data["items"]) == 20


def test_detail_persists_ratings_and_official(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(m, "_http_get_url", _fixture_fetcher([]))

    res = m.detail("20.0/mcp_server_odoo", db_path=db)
    assert res.ok
    row = m.get_module("20.0/mcp_server_odoo", db)
    assert row["rating_value"] == 5.0
    assert row["rating_count"] == 7
    assert row["description_html"]
    assert row["official"] is False

    res = m.detail("20.0/it_hardware", db_path=db)
    assert res.ok
    assert m.get_module("20.0/it_hardware", db)["official"] is True

    # gh branch: unknown id fails cleanly
    assert not m.detail("gh:nope/nope:x", db_path=db).ok
    assert not m.detail("bogus-id", db_path=db).ok


def test_stats_and_categories_and_featured(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(m, "_http_get_url", _fixture_fetcher([]))

    res = m.stats(db_path=db)
    assert res.ok
    assert res.data["site_total"] == 93901
    assert res.data["base_url"] == m.base_url()

    res = m.categories(db_path=db)
    assert res.ok and "Accounting" in res.data

    res = m.sync_featured(db_path=db)
    assert res.ok
    assert "20.0/shopify_ept" in res.data["featured"]
    assert m.get_module("20.0/shopify_ept", db)["featured"] is True

    # offline stats: cache + local counts, no crash
    def boom(url):
        raise OSError("offline")
    monkeypatch.setattr(m, "_http_get_url", boom)
    res = m.stats(db_path=db)
    assert res.ok and res.data["site_total"] == 93901  # stale cache


# -------------------------------------------------------------- github index


def _make_git_repo(repo: Path, tech: str = "demo_module"):
    repo.mkdir(parents=True, exist_ok=True)
    mod = repo / tech
    (mod / "models").mkdir(parents=True, exist_ok=True)
    (mod / "__init__.py").write_text("")
    (mod / "models/__init__.py").write_text("")
    (mod / "__manifest__.py").write_text(
        "{'name': 'Demo Module', 'version': '17.0.1.0.0', "
        "'summary': 'Demo of indexing', 'depends': ['base', 'mail'], "
        "'author': 'Test Author', 'category': 'Tools', "
        "'license': 'LGPL-3', 'description': 'Long demo.'}")
    (mod / "README.md").write_text("# Demo Module\n\nIndexed fixture.\n")
    (repo / "README.md").write_text("# demo repo\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=repo, check=True, capture_output=True)


def test_index_github_repo(tmp_path, monkeypatch):
    db = _db(tmp_path)
    gh_root = tmp_path / "gh"
    _make_git_repo(gh_root / "OCA" / "demo")
    monkeypatch.setenv("ODOO_VITE_GITHUB_BASE", f"file://{gh_root}")
    monkeypatch.setenv("ODOO_VITE_MARKETPLACE_CACHE", str(tmp_path / "cache"))

    lines = []
    res = m.index("OCA/demo", progress_cb=lines.append, db_path=db)
    assert res.ok, res.message
    assert res.data["modules"] == ["demo_module"]
    assert res.data["branch"] == "main"
    assert any("Indexed demo_module" in ln for ln in lines)

    row = m.get_module("gh:OCA/demo:demo_module", db)
    assert row is not None
    assert row["source"] == "github"
    assert row["title"] == "Demo Module"
    assert row["summary"] == "Demo of indexing"
    assert row["series"] == "17.0"
    assert row["depends"] == ["base", "mail"]
    assert row["free"] is True
    assert "Indexed fixture" in row["description_html"]

    # re-index (refresh path) stays green
    res = m.index("OCA/demo", branch="main", db_path=db)
    assert res.ok, res.message

    # detail via gh id: sanitized readme + normalized deps
    res = m.detail("gh:OCA/demo:demo_module", db_path=db)
    assert res.ok
    assert res.data["depends"] == [
        {"label": "base", "tech": "base"}, {"label": "mail", "tech": "mail"}]
    assert res.data["local_reviews"] == []


def test_index_github_validation(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setenv("ODOO_VITE_MARKETPLACE_CACHE", str(tmp_path / "cache"))
    assert not m.index("not a repo", db_path=db).ok
    assert not m.index("", db_path=db).ok
    assert not m.index("justonename", db_path=db).ok
    # unreachable repo (github base points nowhere)
    monkeypatch.setenv("ODOO_VITE_GITHUB_BASE", f"file://{tmp_path / 'none'}")
    assert not m.index("ghost/nothing", db_path=db).ok
    # cancel before any work
    monkeypatch.setenv("ODOO_VITE_GITHUB_BASE", "https://github.com")
    assert not m.index("OCA/web", cancel=lambda: True, db_path=db).ok


def test_index_github_traversal_guard(tmp_path, monkeypatch):
    """3.3.0 P0: owner/repo input must never escape the cache dir.

    The old regex accepted `../..`, so dest resolved to the parent of the
    cache (the app data dir) and the refresh path rmtree'd it before the
    clone even ran.
    """
    db = _db(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("ODOO_VITE_MARKETPLACE_CACHE", str(cache))
    monkeypatch.setenv("ODOO_VITE_GITHUB_BASE", "https://github.com")
    cache.mkdir()
    marker = tmp_path / "keepme.txt"
    marker.write_text("x")

    for evil in ("../..", "..", "a/..", "OCA/..", "x/../../y",
                 "https://github.com/../..", "/../.."):
        res = m.index(evil, db_path=db)
        assert not res.ok, f"accepted {evil!r}"
    assert marker.is_file(), "traversal deleted files outside the cache"
    assert cache.is_dir()

    # branch names are git argv too — reject option-like / traversal forms
    res = m.index("OCA/demo", branch="--upload-pack=/bin/sh", db_path=db)
    assert not res.ok and "branch" in res.message.lower()
    res = m.index("OCA/demo", branch="../../x", db_path=db)
    assert not res.ok and "branch" in res.message.lower()


# ---------------------------------------------------------------- zip import


def _fake_instance(tmp_path, db, name="MkT"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    conf = root / "odoo.conf"
    (root / "core_addons").mkdir(exist_ok=True)
    conf.write_text("[options]\naddons_path = %s\n" % (root / "core_addons"))
    inst = Instance(name=name, version="17.0", mode="managed",
                    path=str(root), conf_path=str(conf))
    assert create_instance(inst, db).ok
    return inst


def test_import_addon_zip_happy(tmp_path):
    db = _db(tmp_path)
    inst = _fake_instance(tmp_path, db)
    res = m.import_addon_zip(FIXTURES / "sample_addon.zip", inst.id, db)
    assert res.ok, res.message
    assert res.data["modules"] == ["sample_mkt_addon"]
    mod_dir = Path(inst.path) / "addons" / "sample_mkt_addon"
    assert (mod_dir / "__manifest__.py").is_file()
    assert (mod_dir / "static" / "description" / "icon.png").is_file()

    # addons_path registered + conf rewritten
    from odoo_vite.core.registry import get_instance
    stored = get_instance(inst.id, db)
    paths = [e["path"] for e in
             (stored.addons_state or [])]
    assert str(Path(inst.path) / "addons") in paths
    conf_text = Path(inst.conf_path).read_text()
    assert str(Path(inst.path) / "addons") in conf_text

    # second import collides (no overwrite)
    res = m.import_addon_zip(FIXTURES / "sample_addon.zip", inst.id, db)
    assert not res.ok and "already exists" in res.message


def test_import_zip_rejects_bad_archives(tmp_path):
    db = _db(tmp_path)
    inst = _fake_instance(tmp_path, db)

    not_zip = tmp_path / "plain.txt"
    not_zip.write_text("i am not a zip")
    assert not m.import_addon_zip(not_zip, inst.id, db).ok

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("mod/__manifest__.py", "{}")
    res = m.import_addon_zip(evil, inst.id, db)
    assert not res.ok and "Unsafe" in res.message

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("just_a_readme.txt", "hi")
    res = m.import_addon_zip(empty, inst.id, db)
    assert not res.ok and "No Odoo module" in res.message

    assert not m.import_addon_zip(tmp_path / "missing.zip", inst.id, db).ok
    assert not m.import_addon_zip(
        FIXTURES / "sample_addon.zip", "no-such-instance", db).ok


# ------------------------------------------------- watch_download (P4 zip)


def test_downloads_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(m.DOWNLOADS_DIR_ENV, str(tmp_path))
    assert m.downloads_dir() == tmp_path


def test_local_reviews_carry_source_annotation(tmp_path):
    db = _db(tmp_path)
    assert m.add_review("17.0/a", 4, "t", "b", "ann", db).ok
    rows = m.list_reviews("17.0/a", db)
    assert rows and rows[0]["source"] == "local"
    assert m.delete_review(rows[0]["id"], db).ok


def test_watch_download_existing_zip(tmp_path, monkeypatch):
    monkeypatch.setenv(m.DOWNLOADS_DIR_ENV, str(tmp_path))
    z = tmp_path / "pos_negative_stock_restrict_1.0.zip"
    z.write_bytes(b"PK\x03\x04payload")
    res = m.watch_download("pos_negative", timeout=5)
    assert res.ok
    assert res.data["path"] == str(z)
    assert res.data["size"] > 0


def test_watch_download_new_zip_arrives(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setenv(m.DOWNLOADS_DIR_ENV, str(tmp_path))
    target = tmp_path / "kanban_app.zip"

    def writer():
        time.sleep(0.6)
        target.write_bytes(b"PK\x03\x04arrived")

    threading.Thread(target=writer, daemon=True).start()
    lines = []
    res = m.watch_download("kanban", timeout=8, progress_cb=lines.append)
    assert res.ok and res.data["path"] == str(target)
    assert any("Watching" in ln for ln in lines)
    assert any("Found kanban_app.zip" in ln for ln in lines)


def test_watch_download_timeout_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv(m.DOWNLOADS_DIR_ENV, str(tmp_path))
    res = m.watch_download("nothing_here", timeout=1.5)
    assert not res.ok and "within 1" in res.message

    assert not m.watch_download("", timeout=5).ok  # empty pattern
    res = m.watch_download("x", timeout=30, cancel=lambda: True)
    assert not res.ok and "Cancelled" in res.message


def test_watch_download_ignores_partial(tmp_path, monkeypatch):
    monkeypatch.setenv(m.DOWNLOADS_DIR_ENV, str(tmp_path))
    (tmp_path / "some_app.zip.part").write_bytes(b"half")
    res = m.watch_download("some_app", timeout=1.5)
    assert not res.ok  # .part is not picked up (glob is *.zip only)
