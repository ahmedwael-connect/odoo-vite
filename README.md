# Odoo Vite — v1.0.0 (Phase 1 + 1.5 Hardening + RC)

Native GTK4/libadwaita Ubuntu desktop app that manages the full lifecycle of
local Odoo instances — "Docker Desktop, but for Odoo". Create, start, stop,
restart, adopt, remove instances; switch/track databases; least-privilege
managed mode; OS-keyring secrets.

Status: Phase 1, Phase 1.5 hardening, and the RC matrix all pass
(`pytest tests/` → 85 green, plus live E2E runs on real Ubuntu).

## Requirements

- Ubuntu 22.04+ (24.04 recommended), Python 3.11+
- PostgreSQL 12+ (server + client)
- A Secret Service for password storage (`gnome-keyring` — password login,
  not auto-login, so it unlocks)

## Install

```bash
# 1. System packages (GTK, Postgres, build tools, Odoo runtime deps).
#    NOTE on Ubuntu 24.04: install `npm` (it pulls the Node runtime) but do
#    NOT apt-install `nodejs` and `npm` together — the two debs conflict and
#    apt aborts the whole transaction. Need newer Node? Use NodeSource
#    (deb.nodesource.com) instead of Ubuntu's nodejs package.
sudo apt install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  python3-venv python3-pip git postgresql postgresql-client \
  libpq-dev wkhtmltopdf npm build-essential \
  libxml2-dev libxslt1-dev libjpeg-dev libsasl2-dev libldap2-dev \
  libssl-dev zlib1g-dev gnome-keyring

# 2. Get the app (tarball from the release, or clone)
tar xzf odoo-vite-1.0.0.tar.gz && cd odoo-vite-1.0.0
# or: git clone <repo-url> && cd odoo-vite

# 3. Python dependencies (PEP 668-safe: use --break-system-packages on
#    Ubuntu 24.04+, or a venv created with --system-site-packages so the
#    system PyGObject stays importable)
pip install --break-system-packages -r requirements.txt

# 4. Run (from the extracted folder root — note: `python3`, not `python`,
#    which Ubuntu does not ship by default)
python3 main.py            # or: python3 -m odoo_vite.main
```

> **Qt migration preview (PSQ):** `python3 -m odoo_vite.ui_qt.main_qt`
> launches the in-progress Qt6 frontend (empty shell reading the same
> registry). `python3 main.py` remains the GTK app and the default —
> use it for all real work until the PSQ-10 cutover. Under X/Xvfb the Qt
> build needs `LD_LIBRARY_PATH=$HOME/.local/usr/lib/x86_64-linux-gnu`
> (user-space `libxcb-cursor0`, see `docs/qt-architecture.md`); tests use
> `QT_QPA_PLATFORM=offscreen` and need nothing extra.

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

`odoo_vite/` package with strict `ui/` (GTK only) + `core/` (pure Python,
zero GTK imports — enforced by `tests/test_no_gtk_in_core.py`) separation.
`tests/` lives at the project root, and a root `main.py` shim forwards to
`odoo_vite.main`. PM specs for each sprint are kept under
`development phases/` for history.
