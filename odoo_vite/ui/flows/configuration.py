"""Configuration flows (Sprint R — moved verbatim from MainWindow).

Conf read/edit/regenerate/restore, metadata save, Addon Path Manager dialog.
`self.win` is the MainWindow.
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui import Adw, HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import (  # noqa: E402
    _finish_alert,
    bind_check_highlight,
)


class ConfigurationFlows:
    """See module docstring. Constructed once: ConfigurationFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _conf_save_flow(self, instance_id: str, changes: dict) -> None:
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              "Instance disappeared")
                return
            res = conf_manager.update_conf_keys(inst.conf_path, changes or {})
            GLib.idle_add(self._show_conf_saved, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _show_conf_saved(self, instance_id: str, ok: bool, message: str) -> bool:
        self.win._show_result(instance_id, ok, message)
        if ok:
            try:
                if self.win.detail.instance_id == instance_id:
                    self.win.detail.refresh_conf()
                    if self.win.detail._running:
                        self.win.detail.lbl_conf_notice.set_text(
                            "Saved — takes effect on next restart "
                            "(instance is running).")
                    else:
                        self.win.detail.lbl_conf_notice.set_text("")
            except Exception:
                pass
        return False


    def _conf_restore_flow(self, instance_id: str) -> None:
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              "Instance disappeared")
                return
            res = conf_manager.restore_conf_backup(inst.conf_path)
            GLib.idle_add(self._show_conf_saved, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _conf_regenerate_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._select_row(instance_id)

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import conf_manager

                res = conf_manager.regenerate_conf(inst)
                GLib.idle_add(self._show_conf_saved, instance_id, res.ok,
                              res.message)

            threading.Thread(target=_work, daemon=True).start()

        self.win._confirm_async(
            "Regenerate odoo.conf from registry?",
            "This OVERWRITES manual key edits with a fresh [options] built "
            "purely from registry fields (port, db user, addons paths). "
            "Unknown sections are preserved; a backup is taken first. "
            "Manual edits not reflected in the registry WILL be lost. Proceed?",
            "Regenerate", _go, destructive=True)


    def _meta_save_flow(self, instance_id: str, meta: dict) -> None:
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              "Instance disappeared")
                return
            # v2 verification: workers>0 pre-check runs before ANY write —
            # a busy longpolling port refuses the whole save (registry and
            # conf stay consistent) with the holder named.
            pre = conf_manager.check_workers_prereqs(
                int(meta.get("workers", 0) or 0), inst.conf_path)
            if not pre.ok:
                GLib.idle_add(self.win._show_result, instance_id, False, pre.message)
                return
            res = update_instance(
                instance_id,
                description=meta.get("description", ""),
                workers=int(meta.get("workers", 0) or 0),
                log_level=meta.get("log_level", "info"),
                python_binary=meta.get("python_binary", ""))
            if not res.ok:
                GLib.idle_add(self.win._show_result, instance_id, False, res.message)
                return
            # workers/log_level are real conf keys: same validated path.
            conf_res = conf_manager.update_conf_keys(inst.conf_path, {
                "workers": str(int(meta.get("workers", 0) or 0)),
                "log_level": meta.get("log_level", "info"),
            })
            if not conf_res.ok:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              f"Metadata saved, but conf write failed: "
                              f"{conf_res.message}")
                return
            GLib.idle_add(self.win._show_result, instance_id, True,
                          "Metadata saved (registry + odoo.conf)")
            GLib.idle_add(self.win.db_ops._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _addons_manage_dialog(self, instance_id: str) -> None:
        from odoo_vite.core import addon_paths

        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._select_row(instance_id)
        try:
            entries = addon_paths.get_addons_state(inst)
        except Exception as exc:
            self.win.toast(f"Cannot load addon paths: {exc}")
            return

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(Gtk.Label(
            label="Order matters (first match wins in Odoo). Unchecked paths "
                  "stay listed but are excluded from addons_path. Every Apply "
                  "rewrites the conf through the validated path.",
            xalign=0, wrap=True))
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(220)
        scrolled.set_max_content_height(360)
        outer.append(scrolled)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.set_child(listbox)

        rows: list[dict] = []

        def _rebuild() -> None:
            # Sync current toggle states back first, or a move/remove would
            # silently drop them (rows are rebuilt from `entries`).
            for existing in rows:
                try:
                    existing["entry"]["enabled"] = bool(
                        existing["check"].get_active())
                except Exception:
                    pass
            while True:
                row = listbox.get_row_at_index(0) if hasattr(
                    listbox, "get_row_at_index") else None
                if row is None:
                    break
                listbox.remove(row)
            for child in list(listbox):
                listbox.remove(child)
            rows.clear()
            for entry in entries:
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                check = Gtk.CheckButton(active=bool(entry.get("enabled", True)))
                bind_check_highlight(check)
                hbox.append(check)
                lbl = Gtk.Label(label=entry.get("path", ""), xalign=0, hexpand=True,
                                wrap=True)
                lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                lbl.set_max_width_chars(46)
                # F2.2: full path on hover — very long paths still truncate.
                lbl.set_tooltip_text(entry.get("path", ""))
                hbox.append(lbl)
                btn_edit = Gtk.Button(label="Edit")
                btn_edit.set_tooltip_text(
                    "Change this path string (e.g. folder renamed/moved)")
                btn_edit.connect("clicked", self._addons_edit, entries, entry,
                                 _rebuild)
                hbox.append(btn_edit)
                btn_up = Gtk.Button(label="↑")
                btn_up.connect("clicked", self._addons_move, entries, entry,
                               -1, _rebuild)
                hbox.append(btn_up)
                btn_down = Gtk.Button(label="↓")
                btn_down.connect("clicked", self._addons_move, entries, entry,
                                 1, _rebuild)
                hbox.append(btn_down)
                btn_rm = Gtk.Button(label="Remove")
                btn_rm.connect("clicked", self._addons_remove, entries, entry,
                               _rebuild)
                hbox.append(btn_rm)
                listbox.append(hbox)
                rows.append({"entry": entry, "check": check, "box": hbox})

        def _collect() -> list:
            return [{"path": r["entry"]["path"],
                     "enabled": bool(r["check"].get_active())} for r in rows]

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_row.append(Gtk.Label(label="Add folder:"))
        path_lbl = Gtk.Label(xalign=0, hexpand=True)
        path_lbl.add_css_class("dim-label")
        path_lbl.set_text("—")
        add_row.append(path_lbl)
        picked: dict = {}

        def _browse(_btn) -> None:
            if not hasattr(Gtk, "FileDialog"):
                self.win.toast("Folder picker unavailable")
                return
            dlg = Gtk.FileDialog(title="Select addons folder")
            dlg.select_folder(
                self.win, None,
                lambda d, t: _folder_picked(d, t, picked, path_lbl))

        def _folder_picked(dlg, result, picked, path_lbl) -> None:
            try:
                folder = dlg.select_folder_finish(result)
                path = folder.get_path() if folder else None
            except Exception:
                return
            if not path:
                return
            picked["path"] = path
            path_lbl.set_text(path)
            if not addon_paths.looks_like_addons_folder(path):
                self.win.toast("Warning: no subfolder with __manifest__.py found — "
                           "you may still add it")

        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", _browse)
        add_row.append(btn_browse)

        def _add(_btn) -> None:
            path = (picked.get("path") or "").strip()
            if not path:
                self.win.toast("Browse for a folder first")
                return
            if path in [e["path"] for e in entries]:
                self.win.toast("Already in the list")
                return
            entries.append({"path": path, "enabled": True})
            picked.clear()
            path_lbl.set_text("—")
            _rebuild()

        btn_add = Gtk.Button(label="Add")
        btn_add.connect("clicked", _add)
        add_row.append(btn_add)
        outer.append(add_row)
        _rebuild()

        def _apply(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()

            def _work() -> None:
                res = addon_paths.apply_addons_state(instance_id, _collect())
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self.win.db_ops._refresh_detail_dbs, instance_id)
                if res.ok:
                    GLib.idle_add(self._refresh_conf_tab, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=f"Addon paths — {inst.name}", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Apply changes")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            # F2.2: wide enough for realistic 46-char paths + row buttons;
            # height still follows content; user-resizable (never disabled).
            # NOTE: Adw.AlertDialog.set_content_width segfaults on libadwaita
            # 1.5 (verified), so the width floor lives on the list scroller.
            scrolled.set_min_content_width(620)
            dlg.set_extra_child(outer)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self.win, None,
                       lambda d, t: _apply(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win.toast("Addon manager needs libadwaita dialogs")


    def _addons_edit(self, _btn, entries: list, entry: dict,
                     rebuild=None) -> None:
        """9.3 Edit: change an entry's path string in place (rename/move)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(label="New path for this entry:", xalign=0))
        field = Gtk.Entry(text=entry.get("path", ""))
        box.append(field)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            new_path = (field.get_text() or "").strip()
            # NOTE: AlertDialog closes on response, so validation failures
            # toast (they couldn't stay visible in a closed dialog).
            if not new_path:
                self.win.toast("Path must not be empty — edit cancelled")
                return
            if new_path != entry.get("path") and new_path in [
                    e.get("path") for e in entries]:
                self.win.toast("That path is already in the list — edit cancelled")
                return
            entry["path"] = new_path
            from odoo_vite.core import addon_paths

            if not addon_paths.looks_like_addons_folder(new_path):
                self.win.toast("Saved — note: no subfolder with __manifest__.py "
                           "found there")
            if rebuild is not None:
                try:
                    rebuild()
                except Exception:
                    pass
            try:
                _dlg_holder["dlg"].close()
            except Exception:
                pass

        _dlg_holder: dict = {}
        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading="Edit addon path", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Save")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            _dlg_holder["dlg"] = dlg
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win.toast("Edit dialog unavailable on this GTK version")


    def _addons_move(self, _btn, entries: list, entry: dict, delta: int,
                     rebuild=None) -> None:
        try:
            idx = entries.index(entry)
        except ValueError:
            return
        other = idx + delta
        if 0 <= other < len(entries):
            entries[idx], entries[other] = entries[other], entries[idx]
            if rebuild is not None:
                try:
                    rebuild()
                except Exception:
                    pass


    def _addons_remove(self, _btn, entries: list, entry: dict,
                       rebuild=None) -> None:
        try:
            entries.remove(entry)
        except ValueError:
            pass
        if rebuild is not None:
            try:
                rebuild()
            except Exception:
                pass


    def _refresh_conf_tab(self, instance_id: str) -> bool:
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.refresh_conf()
            except Exception:
                pass
        return False

    # --------------------------------------- Sprint 8 Monitoring flows


