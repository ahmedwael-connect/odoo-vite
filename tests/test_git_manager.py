"""Ticket 1.4 test: live Odoo branch listing (needs network to github.com)."""

from odoo_vite.core import git_manager


def test_list_branches_contains_stable():
    res = git_manager.list_odoo_branches(refresh=True)
    assert res.ok, res.message
    assert "17.0" in res.data, f"17.0 missing from: {res.data[:10]}"


def test_search_filter_narrows():
    git_manager.clear_cache()
    all_res = git_manager.list_odoo_branches()
    assert all_res.ok, all_res.message
    filt = git_manager.list_odoo_branches(search="16")
    assert filt.ok
    assert len(filt.data) < len(all_res.data)
    assert all("16" in b for b in filt.data)


def test_cache_serves_without_network():
    # Second call hits the in-memory cache (no subprocess).
    git_manager.clear_cache()
    first = git_manager.list_odoo_branches()
    assert first.ok
    second = git_manager.list_odoo_branches()
    assert second.ok
    assert "(cached)" in second.message
