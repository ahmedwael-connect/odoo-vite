# Odoo Vite — Slint frontend (v3.0.0 cutover)

Native Slint Ubuntu desktop app that manages the full lifecycle of
local Odoo instances — "Docker Desktop, but for Odoo". Create, start, stop,
restart, adopt, remove, clone, export/import instances; switch/track databases; least-privilege
managed mode; OS-keyring secrets.

Status: Qt → Slint migration complete (PSS-1..9);
`pytest tests/` green plus live E2E runs on real Ubuntu.

## Requirements

- Ubuntu 22.04+ (24.04 recommended), Python 3.12+
- PostgreSQL 12+ (server + client; 13+ required for Odoo 19.0 — see
  `core/system_check.py` VERSION_REQUIREMENTS)
- A Secret Service for password storage (`gnome-keyring` — password login,
  not auto-login, so it unlocks)

## Install

```bash
# 1. System packages (Postgres, build tools, Odoo runtime deps).
#    NOTE on Ubuntu 24.04: install `npm` (it pulls the Node runtime) but do
#    NOT apt-install `nodejs` and `npm` together — the two debs conflict and
#    apt aborts the whole transaction. Need newer Node? Use NodeSource
#    (deb.nodesource.com) instead of Ubuntu's nodejs package.
sudo apt install -y \
  python3-venv python3-pip git postgresql postgresql-client \
  libpq-dev wkhtmltopdf npm build-essential \
  libxml2-dev libxslt1-dev libjpeg-dev libsasl2-dev libldap2-dev \
  libssl-dev zlib1g-dev gnome-keyring

# 2. Get the app (tarball attached to the GitHub release, or clone)
tar xzf odoo-vite-3.0.0.tar.gz && cd odoo-vite-3.0.0
# or: git clone <repo-url> && cd odoo-vite
# NOTE: *.tar.gz and odoo-vite-*/ are local release artifacts (git-ignored),
# never committed — download them from the release page.

# 3. Python dependencies (PEP 668-safe: use --break-system-packages on
#    Ubuntu 24.04+, or a venv)
pip install --break-system-packages ".[test]"

# 4. Run (from the extracted folder root — note: `python3`, not `python`,
#    which Ubuntu does not ship by default)
python3 main.py            # or: python3 -m odoo_vite.main (Slint app)
```

On first launch the app creates `~/.local/share/odoo-vite/` (registry +
audit log + instance folders). Open **Preferences** to choose Developer vs
Managed provisioning mode before creating instances.

Optional desktop launcher:

```bash
make install-desktop   # writes ~/.local/share/applications/odoo-vite.desktop
```

## Test

```bash
python3 -m pytest tests/ -q
```

(Live Postgres tests skip automatically when no server is reachable.)

## Layout

`odoo_vite/` package with strict `ui_slint/` (Slint markup + Python
bridge) + `core/` (pure Python, zero GUI imports — enforced by
`tests/test_no_slint_in_core.py`) separation.
`tests/` lives at the project root, and a root `main.py` shim forwards to
`odoo_vite.main`. PM specs for each sprint are kept under
`development phases/` for history; the Slint migration log lives in
`docs/slint-work-log.md`.

## License note

The UI toolkit (Slint) is used under its royalty-free license, which
asks for a visible disclosure: open **About Slint** from the sidebar to
see it.
