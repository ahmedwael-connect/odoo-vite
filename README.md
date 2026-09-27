# Odoo Vite — Qt6 frontend (PSQ-10 cutover)

Native Qt6 Ubuntu desktop app that manages the full lifecycle of
local Odoo instances — "Docker Desktop, but for Odoo". Create, start, stop,
restart, adopt, remove instances; switch/track databases; least-privilege
managed mode; OS-keyring secrets.

Status: GTK → Qt migration complete (PSQ-1..9 + human pass + cutover);
`pytest tests/` green plus live E2E runs on real Ubuntu.

## Requirements

- Ubuntu 22.04+ (24.04 recommended), Python 3.11+
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
#    NOTE for X11/Xvfb display: Qt needs libxcb-cursor0, absent on stock
#    24.04 — user-space workaround documented in docs/qt-architecture.md.
sudo apt install -y \
  python3-venv python3-pip git postgresql postgresql-client \
  libpq-dev wkhtmltopdf npm build-essential \
  libxml2-dev libxslt1-dev libjpeg-dev libsasl2-dev libldap2-dev \
  libssl-dev zlib1g-dev gnome-keyring

# 2. Get the app (tarball attached to the GitHub release, or clone)
tar xzf odoo-vite-1.0.1.tar.gz && cd odoo-vite-1.0.1
# or: git clone <repo-url> && cd odoo-vite
# NOTE: *.tar.gz and odoo-vite-*/ are local release artifacts (git-ignored),
# never committed — download them from the release page.

# 3. Python dependencies (PEP 668-safe: use --break-system-packages on
#    Ubuntu 24.04+, or a venv)
pip install --break-system-packages ".[test]"

# 4. Run (from the extracted folder root — note: `python3`, not `python`,
#    which Ubuntu does not ship by default)
python3 main.py            # or: python3 -m odoo_vite.main (Qt app)
```

On first launch the app creates `~/.local/share/odoo-vite/` (registry +
audit log + instance folders). Open **Preferences** to choose Developer vs
Managed provisioning mode before creating instances.

Optional desktop launcher (`~/.local/share/applications/odoo-vite.desktop`):

```ini
[Desktop Entry]
Type=Application
Name=Odoo Vite
Exec=python3 /path/to/odoo-vite/main.py
Icon=computer
Categories=Development;
```

## Test

```bash
python3 -m pytest tests/ -q
```

(Live Postgres tests skip automatically when no server is reachable.)

## Layout

`odoo_vite/` package with strict `ui_qt/` (Qt only) + `core/` (pure Python,
zero GUI imports — enforced by `tests/test_no_gtk_in_core.py` and
`tests/test_no_pyside_in_core.py`) separation.
`tests/` lives at the project root, and a root `main.py` shim forwards to
`odoo_vite.main`. PM specs for each sprint are kept under
`development phases/` for history.
