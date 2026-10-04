"""Shared test fixtures.

3.3.0 P2 safety net: every filesystem-touching ODOO_VITE_* seam is pointed
at the test's own tmp_path HERE, before any test runs. A test that forgets
`monkeypatch.setenv("ODOO_VITE_DB", ...)` (the deleted Slint-era suite did)
used to write instance rows and audit events into the developer's REAL
registry at ~/.local/share/odoo-vite — six stale rows are still sitting in
it. Individual tests can still override any seam; their setenv wins because
this fixture runs first (same function-scoped monkeypatch).
"""

import os

import pytest

FILESYSTEM_SEAMS = (
    "ODOO_VITE_DB",
    "ODOO_VITE_AUDIT",
    "ODOO_VITE_BACKUPS_DIR",
    "ODOO_VITE_DOWNLOADS_DIR",
    "ODOO_VITE_MARKETPLACE_CACHE",
    "ODOO_VITE_LOCKS",
)


# --- CI / headless hermeticity -----------------------------------------------
def _ensure_working_keyring() -> None:
    """CI runners have no Secret Service (no dbus/gnome-keyring), so the
    keyring plumbing tests (adopt/clone/transfer/migrate/probe) fail on the
    runner while passing on developer machines. If NO backend works, install
    a process-local in-memory one — machines with a real keyring keep it
    (the probe succeeds first, nothing is touched)."""
    try:
        from odoo_vite.core import registry as _registry

        if _registry.keyring_available():
            return
        import keyring
        from keyring.backend import KeyringBackend

        class _MemoryKeyring(KeyringBackend):
            priority = 10.0

            def __init__(self) -> None:
                self._store: dict[tuple[str, str], str] = {}

            def get_password(self, service, username):  # noqa: ANN001
                return self._store.get((service, username))

            def set_password(self, service, username, password):  # noqa: ANN001
                self._store[(service, username)] = password

            def delete_password(self, service, username):  # noqa: ANN001
                if (service, username) not in self._store:
                    raise KeyError(username)
                del self._store[(service, username)]

        keyring.set_keyring(_MemoryKeyring())
    except Exception:
        pass  # never break collection over this — tests will surface it


_ensure_working_keyring()

# Git identity: CI runners ship without user.name/user.email, and commits
# made through production code paths (github.publish_addons → _run_git)
# fail with "empty ident name". Defaults match the test fixtures'
# `-c user.name=OdooVite`; a developer's own identity wins via setdefault.
os.environ.setdefault("GIT_AUTHOR_NAME", "OdooVite")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "odoo-vite@test")
os.environ.setdefault("GIT_COMMITTER_NAME", "OdooVite")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "odoo-vite@test")


@pytest.fixture(autouse=True)
def _isolate_odoo_vite_filesystem_seams(tmp_path, monkeypatch):
    for name in FILESYSTEM_SEAMS:
        monkeypatch.setenv(name, str(tmp_path / f"seam-{name.lower()}"))
    yield
