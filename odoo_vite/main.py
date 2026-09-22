"""GTK Application entrypoint (Ticket 1.1).

Uses Adw.Application + Adw.ApplicationWindow when libadwaita is present,
falls back to plain Gtk.Application + Gtk.ApplicationWindow otherwise.
Run with:  python -m odoo_vite.main   (or ./main.py from the project root)
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False

from odoo_vite.ui.window_main import MainWindow  # noqa: E402

APP_ID = "dev.odoovite.App"


BaseApp = Adw.Application if HAS_ADW else Gtk.Application


class OdooViteApp(BaseApp):  # type: ignore[misc, valid-type]
    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = MainWindow(app=self)
        window.present()
        if hasattr(window, "refresh"):
            window.refresh()


def main(argv: list[str] | None = None) -> int:
    app = OdooViteApp(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
