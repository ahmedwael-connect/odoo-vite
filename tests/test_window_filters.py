"""3.1.0 B3: native file-dialog filter normalization (ui_web.window).

The frontend passes three filter shapes (map key / full expression /
bare extension); only the map key survived production, so every file
picker except the keyed ones opened with a filter matching nothing.
"""

import pytest

pytest.importorskip("webview")

from odoo_vite.ui_web.window import _file_types  # noqa: E402


def test_file_types_map_keys():
    assert _file_types("archives") == (
        "Archive (*.tar *.tar.gz *.tgz *.zip)",)
    assert _file_types("dumps") == ("SQL dump (*.sql *.dump *.gz)",)
    assert _file_types("all") == ()
    assert _file_types("") == ()


def test_file_types_full_expressions_pass_through():
    for expr in ("Postgres dumps (*.dump)",
                 "Postgres dumps (*.dump *.sql *.sql.gz)",
                 "Odoo Vite bundles (*.tar.gz)"):
        assert _file_types(expr) == (expr,)


def test_file_types_bare_extensions_expanded():
    assert _file_types("tar.gz") == ("Files (*.tar.gz)",)
    assert _file_types("zip") == ("Files (*.zip)",)
    assert _file_types("conf") == ("Files (*.conf)",)
    assert _file_types("*.tar.gz") == ("Files (*.tar.gz)",)
