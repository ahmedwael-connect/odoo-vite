"""Scheduled-backup flows (Sprint BK.3): add/delete/toggle/run-now/status.

`self.win` is the MainWindow. Execution reuses
backup_scheduler.run_schedule() — the same entry the headless runner and
the UI Run-Now button share.
"""

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance  # noqa: E402
from odoo_vite.ui import HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import _finish_alert  # noqa: E402

PRESETS = [
    ("Hourly", "0 * * * *"),
    ("Daily 02:00", "0 2 * * *"),
    ("Weekly Sun 02:00", "0 2 * * 0"),
    ("Custom…", ""),
]


class BackupSchedulesFlows:
    """See module docstring. Constructed once: BackupSchedulesFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    # ------------------------------------------------------------ refresh

    def _refresh_schedules(self, instance_id: str) -> None:
        def _work() -> None:
            from odoo_vite.core import backup_scheduler

            try:
                scheds = backup_scheduler.list_schedules(instance_id)
                status = backup_scheduler.timer_status()
            except Exception:
                return
            GLib.idle_add(self._render_schedules, instance_id, scheds,
                           status)
        threading.Thread(target=_work, daemon=True).start()

    def _render_schedules(self, instance_id: str, scheds: list,
                          status: dict) -> bool:
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.refresh_schedules(scheds, status)
            except Exception:
                pass
        return False

    # ------------------------------------------------------------ add

    def _sched_add_dialog(self, instance_id: str) -> None:
        from odoo_vite.core import backup_scheduler

        inst = get_instance(instance_id)
        if inst is None:
            return
        names = list(dict.fromkeys(
            ([inst.primary_db] if inst.primary_db else [])
            + (inst.tracked_dbs or [])))
        if not names:
            self.win.toast("Track a database first")
            return

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(Gtk.Label(label="Databases to back up:", xalign=0))
        checks: dict = {}
        for name in names:
            check = Gtk.CheckButton(label=name, active=True)
            checks[name] = check
            outer.append(check)

        outer.append(Gtk.Label(label="Schedule:", xalign=0))
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        drop = Gtk.DropDown(model=Gtk.StringList.new(
            [label for label, _ in PRESETS]))
        drop.set_selected(1)
        preset_row.append(drop)
        entry_cron = Gtk.Entry(text="0 2 * * *", hexpand=True)
        entry_cron.set_tooltip_text(
            "cron expression: minute hour day month weekday")
        preset_row.append(entry_cron)
        outer.append(preset_row)

        def _on_preset(_drop, _pspec) -> None:
            item = _drop.get_selected_item()
            text = item.get_string() if item is not None else ""
            for label, expr in PRESETS:
                if label == text and expr:
                    entry_cron.set_text(expr)

        drop.connect("notify::selected-item", _on_preset)

        ret_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ret_row.append(Gtk.Label(label="Keep last:"))
        spin_n = Gtk.SpinButton.new_with_range(1, 365, 1)
        spin_n.set_value(7)
        ret_row.append(spin_n)
        ret_row.append(Gtk.Label(label="Keep days (0 = off):"))
        spin_days = Gtk.SpinButton.new_with_range(0, 3650, 1)
        spin_days.set_value(0)
        ret_row.append(spin_days)
        outer.append(ret_row)
        lbl_hint = Gtk.Label(xalign=0, wrap=True)
        lbl_hint.add_css_class("dim-label")
        lbl_hint.set_text(
            "Runs even when Odoo Vite is closed (systemd timer, checked "
            "every minute). Backups land under "
            "~/.local/share/odoo-vite/backups/.")
        outer.append(lbl_hint)

        def _on_ok() -> None:
            from odoo_vite.core import backup_scheduler as _bs

            chosen = [n for n, c in checks.items()
                      if bool(c.get_active())]
            if not chosen:
                self.win.toast("Pick at least one database")
                return
            cron = entry_cron.get_text().strip()
            res = _bs.create_schedule(
                instance_id, chosen, cron,
                retention_n=int(spin_n.get_value()),
                retention_days=int(spin_days.get_value()))
            if not res.ok:
                self.win.toast(res.message)
                return
            timer = _bs.install_timer()
            if not timer.ok:
                self.win.toast(f"Schedule saved; timer issue: {timer.message}")
            else:
                self.win.toast("Schedule saved — scheduler active")
            self._refresh_schedules(instance_id)

        if HAS_ADW and HAS_ALERT:
            from odoo_vite.ui import Adw as _Adw

            dlg = _Adw.AlertDialog(
                heading=f"Scheduled backups — {inst.name}", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Save schedule")
            dlg.set_response_appearance(
                "ok", _Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(outer)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok()
                       if _finish_alert(d, t, "cancel") == "ok" else None)
        else:
            self.win.toast("Schedule editor needs libadwaita dialogs")

    # ------------------------------------------------------------ mutations

    def _sched_delete_flow(self, instance_id: str, schedule_id: str) -> None:
        from odoo_vite.core import backup_scheduler

        def _gone(confirmed: bool) -> None:
            if not confirmed:
                return
            res = backup_scheduler.delete_schedule(schedule_id)
            self.win.toast(res.message)
            self._refresh_schedules(instance_id)

        self.win._confirm_async(
            "Delete this backup schedule?",
            "Scheduled runs stop. Existing backup files are kept.",
            "Delete schedule", _gone)

    def _sched_toggle_flow(self, instance_id: str, schedule_id: str) -> None:
        from odoo_vite.core import backup_scheduler

        sched = backup_scheduler.get_schedule(schedule_id)
        if sched is None:
            self.win.toast("Schedule not found")
            return
        res = backup_scheduler.set_enabled(schedule_id, not sched.enabled)
        self.win.toast(res.message)
        self._refresh_schedules(instance_id)

    def _sched_run_now_flow(self, instance_id: str,
                            schedule_id: str) -> None:
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            from odoo_vite.core import backup_scheduler

            res = backup_scheduler.run_schedule(schedule_id)
            GLib.idle_add(self.win._show_result, instance_id, res.ok,
                           res.message)
            GLib.idle_add(self._refresh_schedules_later, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _refresh_schedules_later(self, instance_id: str) -> bool:
        self._refresh_schedules(instance_id)
        return False

    # ------------------------------------------------------------ file list

    def _backups_browse_dialog(self, instance_id: str) -> None:
        from odoo_vite.core import backup_scheduler as _bs

        inst = get_instance(instance_id)
        if inst is None:
            return
        files = _bs.list_backup_files(inst.name)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(Gtk.Label(
            label="Scheduled backups for this instance (newest first). "
                  "Manual backups saved elsewhere are not listed here.",
            xalign=0, wrap=True))
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(200)
        scrolled.set_max_content_height(380)
        outer.append(scrolled)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.set_child(listbox)
        if not files:
            listbox.append(Gtk.Label(label="No backup files yet.", xalign=0))
        for entry in files:
            meta = entry.get("meta") or {}
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            stamp = datetime.fromtimestamp(entry["mtime"]).strftime(
                "%Y-%m-%d %H:%M")
            kb = entry["size"] // 1024
            lbl = Gtk.Label(
                label=f"{Path(entry['path']).name}  ·  "
                      f"{meta.get('db_name', '?')}  ·  {kb} KB  ·  {stamp}",
                xalign=0, hexpand=True)
            lbl.set_tooltip_text(entry["path"])
            row.append(lbl)
            btn_restore = Gtk.Button(label="Restore")
            btn_restore.connect(
                "clicked", self._backup_restore_from_list, instance_id,
                entry["path"])
            row.append(btn_restore)
            btn_del = Gtk.Button(label="Delete")
            btn_del.add_css_class("destructive-action")
            btn_del.connect(
                "clicked", self._backup_delete_from_list, instance_id,
                entry["path"])
            row.append(btn_del)
            listbox.append(row)

        if HAS_ADW and HAS_ALERT:
            from odoo_vite.ui import Adw as _Adw

            dlg = _Adw.AlertDialog(heading="Backup files", body="")
            dlg.add_response("close", "Close")
            dlg.set_extra_child(outer)
            dlg.set_default_response("close")
            dlg.set_close_response("close")
            dlg.choose(self.win, None, lambda _d, _t: None)
        else:
            self.win.toast("Backup browser needs libadwaita dialogs")

    def _backup_restore_from_list(self, _btn, instance_id: str,
                                  dump_path: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        # Reuse Sprint 5's validated restore dialog (type-to-confirm, drop
        # + recreate) — no second restore path.
        self.win.db_ops._restore_dialog(instance_id, inst, dump_path)

    def _backup_delete_from_list(self, _btn, instance_id: str,
                                 dump_path: str) -> None:
        from odoo_vite.core import backup_scheduler as _bs

        def _gone(confirmed: bool) -> None:
            if not confirmed:
                return
            res = _bs.delete_backup_file(dump_path)
            self.win.toast(res.message)

        self.win._confirm_async(
            "Delete this backup file?",
            f"{Path(dump_path).name}\nThis cannot be undone.",
            "Delete backup", _gone)
