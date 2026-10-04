"""Shared test fixtures.

3.3.0 P2 safety net: every filesystem-touching ODOO_VITE_* seam is pointed
at the test's own tmp_path HERE, before any test runs. A test that forgets
`monkeypatch.setenv("ODOO_VITE_DB", ...)` (the deleted Slint-era suite did)
used to write instance rows and audit events into the developer's REAL
registry at ~/.local/share/odoo-vite — six stale rows are still sitting in
it. Individual tests can still override any seam; their setenv wins because
this fixture runs first (same function-scoped monkeypatch).
"""

import pytest

FILESYSTEM_SEAMS = (
    "ODOO_VITE_DB",
    "ODOO_VITE_AUDIT",
    "ODOO_VITE_BACKUPS_DIR",
    "ODOO_VITE_DOWNLOADS_DIR",
    "ODOO_VITE_MARKETPLACE_CACHE",
    "ODOO_VITE_LOCKS",
)


@pytest.fixture(autouse=True)
def _isolate_odoo_vite_filesystem_seams(tmp_path, monkeypatch):
    for name in FILESYSTEM_SEAMS:
        monkeypatch.setenv(name, str(tmp_path / f"seam-{name.lower()}"))
    yield
