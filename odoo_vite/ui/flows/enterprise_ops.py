"""Enterprise flows (Sprint ENT): load dialog + unload confirm.

`self.win` is the MainWindow. Execution reuses core/enterprise.py;
cloning streams into the shared progress dialog (Sprint 2 pattern).
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance  # noqa: E402
from odoo_vite.ui import HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import (  # noqa: E402
    _finish_alert,
    build_progress_dialog,
)


class EnterpriseFlows:
    """See module docstring. Constructed once: EnterpriseFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _refresh_enterprise_badge(self, instance_id: str) -> None:
        try:
            inst = get_instance(instance_id)
            if inst is not None and (
                    self.win.detail.instance_id == instance_id):
                self.win.detail._refresh_enterprise_badge(inst)
        except Exception:
            pass

    def _ent_load_dialog(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(Gtk.Label(
            label="Clone YOUR licensed Enterprise remote (auth is your own "
                  "git/SSH setup — Odoo Vite never handles credentials).",
            xalign=0, wrap=True))
        outer.append(Gtk.Label(label="Git URL:", xalign=0))
        entry_url = Gtk.Entry(
            hexpand=True,
            placeholder_text="git@github.com:<you>/enterprise.git")
        outer.append(entry_url)
        outer.append(Gtk.Label(label="Branch:", xalign=0))
        entry_branch = Gtk.Entry(text=inst.version or "", hexpand=True)
        entry_branch.set_tooltip_text(
            "Defaults to this instance's Odoo version")
        outer.append(entry_branch)

        def _on_ok(url: str, branch: str) -> None:
            if not url.strip():
                self.win.toast("Enter your Enterprise git URL first")
                return
            dlg, append, done = build_progress_dialog(
                self.win, "Load Enterprise")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import enterprise as _ent

                res = _ent.load_enterprise(instance_id, url, branch,
                                           progress_cb=_feed)
                GLib.idle_add(done, res.ok, res.message)
                GLib.idle_add(self.win._show_result, instance_id, res.ok,
                               res.message)
                if res.ok:
                    GLib.idle_add(self._after_ent_changed, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            from odoo_vite.ui import Adw as _Adw

            dlg = _Adw.AlertDialog(
                heading=f"Load Enterprise — {inst.name}", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Clone + wire in")
            dlg.set_response_appearance(
                "ok", _Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(outer)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self.win, None, lambda d, t: _on_ok(
                entry_url.get_text(), entry_branch.get_text())
                if _finish_alert(d, t, "cancel") == "ok" else None)
        else:
            self.win.toast("Enterprise loader needs libadwaita dialogs")

    def _after_ent_changed(self, instance_id: str) -> bool:
        self._refresh_enterprise_badge(instance_id)
        try:
            self.win.config_flows._refresh_conf_tab(instance_id)
        except Exception:
            pass
        return False

    def _ent_unload_flow(self, instance_id: str) -> None:
        def _gone(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import enterprise as _ent

                res = _ent.unload_enterprise(instance_id)
                GLib.idle_add(self.win._show_result, instance_id, res.ok,
                               res.message)
                if res.ok:
                    GLib.idle_add(self._after_ent_changed, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        self.win._confirm_async(
            "Unload Enterprise?",
            "This instance will stop using Enterprise addons. "
            "The cloned files themselves are NOT deleted.",
            "Unload (keep files)", _gone)
