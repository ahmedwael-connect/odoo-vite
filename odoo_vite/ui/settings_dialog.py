"""Settings dialog (Phase 1.5, Ticket H.1).

Currently: provisioning_mode (Developer = CREATEDB role, Managed =
least-privilege role with explicit privileged DB create/drop). Async —
UI-initiated, no background threads needed (SQLite key/value read/write).
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
    HAS_ALERT = hasattr(Adw, "AlertDialog")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_ALERT = False

from odoo_vite.core.settings import (  # noqa: E402
    VALID_MODES,
    get_provisioning_mode,
    set_provisioning_mode,
)

MODE_LABELS = {
    "developer": "Developer (role gets CREATEDB — convenient, broad grant)",
    "managed": "Managed (least-privilege role — DB create/drop prompt separately)",
}
MODE_ORDER = ["developer", "managed"]


def show_settings_dialog(parent, on_changed=None) -> None:
    """Present the settings dialog. on_changed() fires after a save."""
    current = get_provisioning_mode()

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(Gtk.Label(label="Provisioning mode for NEW instances "
                               "(existing rows keep their stored mode):",
                         xalign=0, wrap=True))
    dropdown = Gtk.DropDown(model=Gtk.StringList.new(
        [MODE_LABELS[m] for m in MODE_ORDER]))
    try:
        dropdown.set_selected(MODE_ORDER.index(current))
    except ValueError:
        dropdown.set_selected(0)
    box.append(dropdown)
    hint = Gtk.Label(xalign=0, wrap=True)
    hint.add_css_class("dim-label")
    hint.set_text(
        "Managed instances get a Postgres role WITHOUT CREATEDB. "
        "Database creation and drops then ask for elevation at the moment "
        "of need instead of holding a standing grant.")
    box.append(hint)

    def _save(_confirmed: bool = True) -> None:
        mode = MODE_ORDER[dropdown.get_selected()]
        res = set_provisioning_mode(mode)
        if res.ok and on_changed is not None:
            try:
                on_changed()
            except Exception:
                pass

    if HAS_ADW and HAS_ALERT:
        dlg = Adw.AlertDialog(heading="Settings", body="")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Save")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_extra_child(box)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.choose(parent, None,
                   lambda d, t: _save() if _alert_ok(d, t) else None)
    else:
        dlg = Gtk.Dialog(title="Settings", transient_for=parent, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Save", Gtk.ResponseType.ACCEPT)
        dlg.get_content_area().append(box)
        dlg.connect("response",
                    lambda d, r: (_save(), d.close()) if r == Gtk.ResponseType.ACCEPT
                    else d.close())
        dlg.present()


def _alert_ok(dlg, task) -> bool:
    try:
        return dlg.choose_finish(task) == "ok"
    except Exception:
        return False
