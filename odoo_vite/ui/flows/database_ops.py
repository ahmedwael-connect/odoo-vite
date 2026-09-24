"""Database operations flows (Sprint R — moved verbatim from MainWindow).

Init/backup/restore/drop/validate + tracked-DB state loading for the
Databases tab. `self.win` is the MainWindow (toasts, dialogs, busy state,
detail/sidebar/polling access).
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core import process_manager  # noqa: E402
from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui import Adw, HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import _finish_alert  # noqa: E402


class DatabaseOpsFlows:
    """See module docstring. Constructed once: DatabaseOpsFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _init_db_flow(self, instance_id: str, db_name: str) -> None:
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            res = process_manager.initialize_database(instance_id, db_name)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _backup_flow(self, instance_id: str, db_name: str) -> None:
        from datetime import datetime, timezone

        inst = get_instance(instance_id)
        if inst is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        default = f"{db_name}_{stamp}.dump"
        if not hasattr(Gtk, "FileDialog"):
            self.win.toast("File picker unavailable on this GTK version")
            return
        dlg = Gtk.FileDialog(title=f"Back up '{db_name}' as…")
        dlg.set_initial_name(default)

        def _chosen(d, result) -> None:
            try:
                target = d.save_finish(result)
                dest = target.get_path() if target else None
            except Exception:
                return
            if not dest:
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import db_backup
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                res = db_backup.backup_database(
                    db_name, dest, db_user=inst.db_user, db_password=pw,
                    instance_id=inst.id, instance_name=inst.name)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        dlg.save(self.win, None, _chosen)


    def _drop_db_flow(self, instance_id: str, db_name: str) -> None:
        """Standalone drop (B.4): primary row has no Drop button at all."""
        inst = get_instance(instance_id)
        if inst is None:
            return
        if db_name == (inst.primary_db or ""):
            self.win.toast("The primary database cannot be dropped here — "
                       "switch primary first, or remove the instance")
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text=f"Type '{db_name}' to confirm")
        box.append(entry)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            if entry.get_text().strip() != db_name:
                self.win.toast("Name did not match — drop cancelled")
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import db_manager
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                # Part A: verify it exists first — never drop blind.
                from odoo_vite.core.db_state import get_db_state

                if not get_db_state(db_name, inst.db_user, pw).exists:
                    GLib.idle_add(self.win._show_result, instance_id, True,
                                  f"'{db_name}' does not exist — nothing to drop")
                    return
                res = db_manager.drop_database(db_name, inst.db_user, pw)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading=f"Drop database '{db_name}'?",
                body="This permanently deletes the database. The instance "
                     "keeps running on its primary; the name stays tracked "
                     "(untrack it separately if you no longer want it listed).")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Drop permanently")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win._confirm_async(
                f"Drop database '{db_name}'?",
                "Type-to-confirm unavailable here — action cancelled.",
                "Drop", lambda ok: None, destructive=True)


    def _restore_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None or not hasattr(Gtk, "FileDialog"):
            if inst is not None:
                self.win.toast("File picker unavailable on this GTK version")
            return
        picker = Gtk.FileDialog(title="Select dump file to restore")

        def _file_chosen(d, result) -> None:
            try:
                picked = d.open_finish(result)
                dump = picked.get_path() if picked else None
            except Exception:
                return
            if dump:
                self._restore_dialog(instance_id, inst, dump)

        picker.open(self.win, None, _file_chosen)


    def _restore_dialog(self, instance_id: str, inst, dump: str) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(
            label="Target database (will be DROPPED and recreated from the "
                  "dump — never merged into live data):", xalign=0, wrap=True))
        entry_target = Gtk.Entry(text=inst.primary_db or "")
        box.append(entry_target)
        entry_confirm = Gtk.Entry(
            placeholder_text="Retype the target name to confirm")
        box.append(entry_confirm)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            target = (entry_target.get_text() or "").strip()
            if not target or entry_confirm.get_text().strip() != target:
                self.win.toast("Names did not match — restore cancelled")
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import db_backup
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                res = db_backup.restore_database(
                    dump, target, db_user=inst.db_user, db_password=pw)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading="Restore database",
                                  body="Validate-first, drop-and-recreate semantics.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Restore (drop + recreate)")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win.toast("Restore dialog unavailable on this GTK version")


    def _validate_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core.db_state import validate_db_config

            try:
                report = validate_db_config(inst)
            except Exception as exc:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              f"Validation crashed: {exc}")
                return
            GLib.idle_add(self._show_validate_report, instance_id, report)
            GLib.idle_add(self.win._op_end)

        threading.Thread(target=_work, daemon=True).start()


    def _show_validate_report(self, instance_id: str, report: dict) -> bool:
        checks = (report or {}).get("checks", [])
        failed = [c for c in checks if c.get("ok") is False]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for check in checks:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = "✅" if check.get("ok") is True else (
                "❌" if check.get("ok") is False else "❔")
            row.append(Gtk.Label(label=icon))
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            vbox.append(Gtk.Label(label=str(check.get("field", "?")), xalign=0))
            detail = Gtk.Label(label=str(check.get("detail", "")), xalign=0, wrap=True)
            detail.add_css_class("dim-label")
            vbox.append(detail)
            row.append(vbox)
            box.append(row)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(min(320, 60 + 48 * max(len(checks), 1)))
        scrolled.set_max_content_height(420)
        scrolled.set_child(box)
        summary = ("All checks passed" if not failed
                   else f"{len(failed)} check(s) failing")
        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=f"Config validation: {summary}", body="")
            dlg.add_response("ok", "Close")
            dlg.set_extra_child(scrolled)
            dlg.choose(self.win, None, lambda d, t: None)
        else:
            self.win._show_result(instance_id, not failed, summary)
        if failed and self.win.detail.instance_id == instance_id:
            self.win.detail.show_error(
                "Config validation: " + "; ".join(
                    f"{c['field']}: {c['detail']}" for c in failed[:3]))
        return False

    # --------------------------------------- Sprint 6 Module Management


    def _refresh_detail_dbs(self, instance_id: str) -> bool:
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.refresh_databases()
            except Exception:
                pass
            # Part B: live per-DB state (bg fetch — never on the main loop).
            threading.Thread(target=self._load_db_states,
                             args=(instance_id,), daemon=True).start()
        return False


    def _load_db_states(self, instance_id: str) -> None:
        from odoo_vite.core.db_state import get_db_state, human_size, odoo_major
        from odoo_vite.core.registry import get_db_password

        try:
            inst = get_instance(instance_id)
            if inst is None:
                return
            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            names = list(dict.fromkeys(
                (inst.tracked_dbs or []) + ([inst.primary_db] if inst.primary_db else [])))
            states: dict = {}
            for name in names:
                try:
                    st = get_db_state(name, inst.db_user, pw)
                except Exception:
                    continue
                states[name] = {
                    "exists": st.exists,
                    "initialized": st.initialized,
                    "odoo_version": st.odoo_version or "",
                    "odoo_major": odoo_major(st.odoo_version),
                    "size": human_size(st.size_bytes),
                    "owner": st.owner or "",
                }
            GLib.idle_add(self._apply_db_states, instance_id,
                           states, inst.version or "")
        except Exception:
            pass


    def _apply_db_states(self, instance_id: str, states: dict,
                         version: str) -> bool:
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_db_states(states, version)
            except Exception:
                pass
        return False


