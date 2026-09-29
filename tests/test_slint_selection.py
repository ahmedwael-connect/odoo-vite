"""PSS-2: selection state contract (pure logic, no UI thread)."""

from odoo_vite.ui_slint.selection import SelectionState, flatten


def _rows():
    return [
        {"id": "a", "title": "Alpha"},
        {"id": "b", "title": "Beta", "group": "Likely"},
        {"id": "c", "title": "Gamma", "group": "Likely"},
        {"id": "d", "title": "Delta", "group": "Other"},
    ]


def test_flatten_groups_with_headers():
    flat = flatten(_rows())
    assert flat[0]["id"] == "a"
    assert flat[1]["header"] is True and flat[1]["title"] == "Likely"
    assert [r["id"] for r in flat[2:4]] == ["b", "c"]
    assert flat[4]["title"] == "Other"


def test_checks_preserved_across_refresh():
    st = SelectionState(multi=True)
    st.set_items([{"id": "a", "title": "Alpha", "checked": True},
                  {"id": "b", "title": "Beta"}])
    assert st.checked_ids() == ["a"]
    # Refresh rebuilds dicts without flags: user pick survives.
    st.set_items([{"id": "a", "title": "Alpha"},
                  {"id": "b", "title": "Beta"}])
    assert st.checked_ids() == ["a"]
    # Explicit flags still win (Discover pre-check parity).
    st.set_items([{"id": "a", "title": "Alpha", "checked": False},
                  {"id": "b", "title": "Beta", "checked": True}])
    assert st.checked_ids() == ["b"]
    # Single-select stays checkless.
    single = SelectionState(multi=False)
    single.set_items([{"id": "a", "title": "A", "checked": True}])
    assert single.checked_ids() == []
    assert single.set_checked("a", True) is False


def test_filter_and_counts():
    st = SelectionState(multi=True)
    st.set_items(_rows())
    st.set_filter("alp")
    visible = [r["id"] for r in st.visible_rows() if not r.get("header")]
    assert visible == ["a"]
    assert "of 4 shown" in st.counts_text()
    st.set_filter("")
    assert len(st.visible_rows()) == 6  # 4 rows + 2 headers
    assert st.counts_text() == "0 checked · 4 of 4 shown"
    st.set_checked("a", True)
    assert st.counts_text().startswith("1 checked")


def test_group_filter_keeps_matching_headers():
    st = SelectionState(multi=True)
    st.set_items(_rows())
    st.set_filter("gamma")
    visible = [r["id"] for r in st.visible_rows()]
    assert visible == ["__group:Likely", "c"]
    st.set_filter("zzz")
    assert st.visible_rows() == []


def test_cursor_survives_refresh():
    st = SelectionState(multi=False)
    st.set_items(_rows())
    assert st.move_current("b")
    st.set_items(_rows())
    assert st.current_id == "b"
    assert st.move_current("__group:Likely") is False
    st.set_items([{"id": "a", "title": "Alpha"}])
    assert st.current_id is None  # vanished row clears cursor


def test_sync_model_updates_in_place():
    """PSS-4: unchanged rows keep identity (no drop → no GC race)."""
    import slint

    from odoo_vite.ui_slint.selection import sync_model

    st = SelectionState(multi=True)
    st.set_items([{"id": "a", "title": "Alpha"},
                  {"id": "b", "title": "Beta"}])
    model = slint.ListModel(st.view_rows())
    first_before = model[0]
    sync_model(model, st.view_rows())  # identical: zero writes
    assert len(model) == 2
    assert model[0] is first_before, "identical sync must keep identity"
    st.set_checked("b", True)
    sync_model(model, st.view_rows())
    assert dict(model[1])["checked"] is True
    st.set_items([{"id": "a", "title": "Alpha"},
                  {"id": "c", "title": "Gamma"}])
    sync_model(model, st.view_rows())
    assert [dict(r)["id"] for r in model] == ["a", "c"]
