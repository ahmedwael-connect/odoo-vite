"""Logs & monitoring flows (Sprint R — moved verbatim from MainWindow).

Log search/doctor, slow queries, py-spy profiling + SVG viewing.
`self.win` is the MainWindow. NOTE: the app event panel (toggle, revealer,
audit polling) deliberately STAYS in window_main.py — it is window-shell
chrome (header control + bottom panel), not a per-instance flow.
"""

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance  # noqa: E402
from odoo_vite.ui import Adw, HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import _finish_alert  # noqa: E402


class LogsMonitoringFlows:
    """See module docstring. Constructed once: LogsMonitoringFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _log_search_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        pattern = ""
        level = None
        try:
            pattern = self.win.detail.entry_log_search.get_text() or ""
            item = self.win.detail.drop_log_level.get_selected_item()
            text = item.get_string() if item is not None else "All levels"
            level = None if text == "All levels" else text
        except Exception:
            pass
        if not pattern.strip():
            self.win.toast("Enter a regex pattern to search")
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import log_search

            res = log_search.search_file(inst.log_path or "", pattern,
                                         level=level, context=2)
            GLib.idle_add(self._show_search_results, instance_id,
                           res.ok, res.message,
                           (res.data or {}).get("matches", []) if res.ok else [])
            GLib.idle_add(self.win._op_end)

        threading.Thread(target=_work, daemon=True).start()


    def _show_search_results(self, instance_id: str, ok: bool, message: str,
                             matches: list) -> bool:
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                if ok:
                    self.win.detail.set_search_results(matches, message)
                else:
                    self.win.detail.set_search_results([], message)
            except Exception:
                pass
        return False


    def _log_doctor_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None or not inst.log_path:
            self.win.toast("No log file recorded for this instance")
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import log_doctor

            try:
                findings = log_doctor.diagnose_file(inst.log_path)
            except Exception as exc:
                GLib.idle_add(self.win._show_result, instance_id, False,
                              f"Doctor failed: {exc}")
                return
            GLib.idle_add(self._show_doctor_findings, instance_id, findings)
            GLib.idle_add(self.win._op_end)

        threading.Thread(target=_work, daemon=True).start()


    def _show_doctor_findings(self, instance_id: str, findings: list) -> bool:
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_doctor_findings(findings)
                if not findings:
                    self.win.toast("Doctor found no known issues in the log")
            except Exception:
                pass
        return False


    def _slow_refresh_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import db_manager
            from odoo_vite.core.registry import get_db_password

            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            if not db_manager.pg_stat_statements_enabled(inst.db_user, pw):
                GLib.idle_add(self._show_slow, instance_id, False,
                              "pg_stat_statements is not available to this role. "
                              "Ask your Postgres admin to run: CREATE EXTENSION "
                              "pg_stat_statements; (first enable needs "
                              "shared_preload_libraries + a server restart — "
                              "Odoo Vite won't do that for you: it restarts "
                              "Postgres for every instance on the box.)", [])
                return
            res = db_manager.slow_queries(inst.primary_db, inst.db_user, pw)
            GLib.idle_add(self._show_slow, instance_id, res.ok, res.message,
                           (res.data or {}).get("queries", []) if res.ok else [])

        threading.Thread(target=_work, daemon=True).start()


    def _show_slow(self, instance_id: str, ok: bool, message: str,
                   rows: list) -> bool:
        # _op_end accounting: _show_result also ends; call exactly one path.
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_slow_queries(ok, message, rows)
            except Exception:
                pass
        self.win._op_end()
        return False


    def _profile_flow(self, instance_id: str, duration: int = 10) -> None:
        from odoo_vite.core import profiler
        from odoo_vite.core.process_manager import _alive_pid

        inst = get_instance(instance_id)
        if inst is None:
            return
        pid = _alive_pid(inst)
        if pid is None:
            self.win._select_row(instance_id)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error(
                    "Profile needs a running process — start the instance first.")
            return
        if profiler.py_spy_path() is None:
            self.win._confirm_async(
                "py-spy is not installed",
                "Flame graphs need py-spy (pip package, user-scoped install, "
                "no sudo required). Install it now with 'pip install py-spy'?",
                "Install py-spy",
                lambda ok: self._install_pyspy_then_profile(instance_id, pid,
                                                            duration)
                if ok else None)
            return
        self._run_profile(instance_id, pid, duration)


    def _install_pyspy_then_profile(self, instance_id: str, pid: int,
                                    duration: int) -> None:
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import profiler

            res = profiler.ensure_py_spy()
            if not res.ok:
                GLib.idle_add(self.win._show_result, instance_id, False, res.message)
                return
            # Op stays held: _run_profile takes it over (balanced by its end).
            GLib.idle_add(self._run_profile, instance_id, pid, duration, True)

        threading.Thread(target=_work, daemon=True).start()


    def _run_profile(self, instance_id: str, pid: int, duration: int,
                     _held: bool = False) -> None:
        import os
        import tempfile

        if not _held:
            self.win._op_start()
        dest = os.path.join(
            tempfile.gettempdir(), f"odoo-vite-profile-{instance_id[:8]}.svg")

        def _work() -> None:
            from odoo_vite.core import profiler

            res = profiler.profile_pid(pid, duration=duration, output_svg=dest)
            GLib.idle_add(self._show_profile_result, instance_id, res.ok,
                           res.message,
                           (res.data or {}).get("svg", "") if res.ok else "")

        threading.Thread(target=_work, daemon=True).start()


    def _show_profile_result(self, instance_id: str, ok: bool, message: str,
                             svg: str) -> bool:
        self.win._op_end()
        if not ok:
            self.win._select_row(instance_id)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error(message)
            return False
        self.win.toast(message)
        self._view_svg(svg)
        return False


    def _view_svg(self, path: str) -> None:
        shown = False
        try:
            from gi.repository import Gio as _Gio

            pic = Gtk.Picture.new_for_filename(path)
            pic.set_can_shrink(True)
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_min_content_height(420)
            scrolled.set_child(pic)
            if HAS_ADW and HAS_ALERT:
                dlg = Adw.AlertDialog(heading="Flame graph", body="")
                dlg.add_response("ok", "Close")
                dlg.set_extra_child(scrolled)
                dlg.set_size_request(720, 520)
                dlg.choose(self, None, lambda d, t: None)
                shown = True
        except Exception:
            shown = False
        if not shown:
            try:
                from gi.repository import Gio as _Gio2

                _Gio2.AppInfo.launch_default_for_uri(
                    Path(path).as_uri(), None)
                self.win.toast(f"Opened {path} externally")
            except Exception:
                self.win.toast(f"Profile saved: {path}")

    # ----------------------------------- Sprint 10 Dev Tools flows


