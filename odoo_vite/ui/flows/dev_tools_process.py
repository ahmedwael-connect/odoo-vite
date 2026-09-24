"""Dev Tools process flows: interactive shell, dev-mode watch, module test runner. (Sprint R — moved verbatim from MainWindow; `self.win` is the MainWindow)."""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui import HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import _finish_alert  # noqa: E402



class DevToolsProcessFlows:
    """Dev Tools process flows: interactive shell, dev-mode watch, module test runner. Constructed once: DevToolsProcessFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _shell_session(self, instance_id: str):
        return (self.win._shell_sessions or {}).get(instance_id)


    def _shell_start_flow(self, instance_id: str) -> None:
        from odoo_vite.core import odoo_shell

        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._select_row(instance_id)
        self.win._op_start()
        if self.win._shell_sessions is None:
            self.win._shell_sessions = {}

        def _work() -> None:
            session = odoo_shell.OdooShell()
            res = session.start(inst)
            GLib.idle_add(self._show_shell_started, instance_id, res.ok,
                           res.message, session if res.ok else None)

        threading.Thread(target=_work, daemon=True).start()


    def _show_shell_started(self, instance_id: str, ok: bool, message: str,
                            session) -> bool:
        self.win._op_end()
        if ok and session is not None:
            self.win._shell_sessions[instance_id] = session
            self._shell_timer_ensure()
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.shell_set_status(message)
            except Exception:
                pass
        if not ok:
            self.win.toast(f"Shell failed: {message[:200]}")
        return False


    def _shell_timer_ensure(self) -> None:
        if not getattr(self, "_shell_timer_id", 0):
            self.win._shell_timer_id = GLib.timeout_add(200, self._shell_poll_tick)


    def _shell_poll_tick(self) -> bool:
        live = False
        for instance_id, session in list((self.win._shell_sessions or {}).items()):
            try:
                lines = session.drain_output()
            except Exception:
                lines = []
            if lines and self.win.detail.instance_id == instance_id:
                try:
                    self.win.detail.shell_append(lines)
                except Exception:
                    pass
            try:
                alive = session.running
            except Exception:
                alive = False
            if alive:
                live = True
            else:
                try:
                    code = session.exit_code
                except Exception:
                    code = "?"
                if self.win.detail.instance_id == instance_id:
                    try:
                        self.win.detail.shell_set_status(
                            f"Shell exited (code {code}) — Start again if needed.")
                    except Exception:
                        pass
                try:
                    del self.win._shell_sessions[instance_id]
                except KeyError:
                    pass
        if not live:
            self.win._shell_timer_id = 0
            return False
        return True


    def _shell_send_flow(self, instance_id: str, text: str) -> None:
        session = self._shell_session(instance_id)
        if session is None:
            self.win.toast("Start the shell first")
            return

        def _work() -> None:
            try:
                res = session.send_line(text or "")
            except Exception as exc:
                GLib.idle_add(self.win.toast, f"Shell send failed: {exc}")
                return
            if not res.ok:
                GLib.idle_add(self.win.toast, f"Shell send failed: {res.message[:160]}")

        threading.Thread(target=_work, daemon=True).start()


    def _shell_stop_flow(self, instance_id: str) -> None:
        session = self._shell_session(instance_id)
        if session is None:
            return

        def _work() -> None:
            try:
                res = session.stop()
            except Exception as exc:
                GLib.idle_add(self.win.toast, f"Shell stop failed: {exc}")
                return
            GLib.idle_add(self.win.toast, res.message if res.ok else
                          f"Shell stop failed: {res.message[:160]}")

        threading.Thread(target=_work, daemon=True).start()

    # --------------------------------------------------- dev mode


    def _devmode_flow(self, instance_id: str, on: bool) -> None:
        if on:
            self._devmode_start(instance_id)
        else:
            self._devmode_stop(instance_id, announce=True)


    def _devmode_start(self, instance_id: str) -> None:
        from odoo_vite.core import devwatch
        from odoo_vite.core.process_manager import _alive_pid

        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._select_row(instance_id)
        if self.win._dev_watches is None:
            self.win._dev_watches = {}
        if instance_id in self.win._dev_watches:
            return  # already watching

        def _prep() -> None:
            # Toggling on while stopped starts the instance (explicit, logged).
            live = _alive_pid(inst) is not None
            if not live:
                res = self._devmode_start_process(inst)
                if res is not None:
                    GLib.idle_add(self.win._show_result, instance_id, False, res)
                    GLib.idle_add(self._sync_devmode_toggle, instance_id, False,
                                   "could not start")
                    return
            GLib.idle_add(self._devmode_attach, instance_id)

        threading.Thread(target=_prep, daemon=True).start()


    def _devmode_start_process(self, inst):
        """Start the instance for dev mode. Returns None on success or an
        error message (runs in a background thread)."""
        from odoo_vite.core import process_manager

        res = process_manager.start_instance(inst.id)
        return None if res.ok else res.message


    def _devmode_attach(self, instance_id: str) -> bool:
        from gi.repository import Gio as _Gio

        from odoo_vite.core import devwatch

        inst = get_instance(instance_id)
        if inst is None:
            return False
        roots = devwatch.watch_roots(inst)
        if not roots:
            self.win.toast("Dev Mode needs at least one addons folder on disk")
            self._sync_devmode_toggle(instance_id, False, "no folders")
            return False
        controller = devwatch.DebounceController()
        monitors = []
        try:
            for root in roots:
                monitors.extend(self._monitor_tree(root, controller))
        except Exception as exc:
            self.win.toast(f"Dev Mode failed to watch: {exc}")
            self._sync_devmode_toggle(instance_id, False, "watch failed")
            return False
        self.win._dev_watches[instance_id] = {
            "controller": controller, "monitors": monitors}
        if not getattr(self, "_devmode_timer_id", 0):
            self.win._devmode_timer_id = GLib.timeout_add(
                250, self._devmode_tick)
        try:
            self.win.detail.set_devmode_state(True, f"watching {len(roots)} folder(s)")
        except Exception:
            pass
        self.win.toast("Dev Mode on — saving watched files restarts the instance")
        return False


    def _monitor_tree(self, root: str, controller) -> list:
        """Attach Gio.FileMonitors recursively (dirs only). Returns handles."""
        from gi.repository import Gio as _Gio

        from odoo_vite.core import devwatch

        handles = []

        def _attach(directory: str) -> None:
            try:
                gfile = _Gio.File.new_for_path(directory)
                monitor = gfile.monitor_directory(_Gio.FileMonitorFlags.NONE,
                                                  None)
            except Exception:
                return

            def _changed(_mon, _file, _other, _event):
                try:
                    path = _file.get_path() if _file is not None else ""
                except Exception:
                    path = ""
                if not path:
                    return
                # new subdirectories join the watch on creation
                if _event in (_Gio.FileMonitorEvent.CREATED,):
                    try:
                        import os as _os

                        if _os.path.isdir(path):
                            _attach(path)
                    except Exception:
                        pass
                try:
                    if devwatch.should_watch(path):
                        controller.feed()
                except Exception:
                    pass

            monitor.connect("changed", _changed)
            handles.append(monitor)

        _attach(root)
        try:
            import os as _os

            for dirpath, dirnames, _files in _os.walk(root):
                # Consistent with devwatch.should_watch: only well-known junk
                # dirs are pruned; dot-parents like ~/.local must stay visible.
                dirnames[:] = [d for d in dirnames if d not in devwatch.SKIP_DIRS]
                for dirname in dirnames:
                    _attach(_os.path.join(dirpath, dirname))
        except Exception:
            pass
        return handles


    def _devmode_tick(self) -> bool:
        if not self.win._dev_watches:
            self.win._devmode_timer_id = 0
            return False

        def _fire(instance_id: str):
            def _work() -> None:
                from odoo_vite.core import audit as _audit
                from odoo_vite.core import process_manager

                _audit.log_event(instance_id, "", "dev_restart",
                                 "dev-mode file change — restarting")
                res = process_manager.restart_instance(instance_id)
                GLib.idle_add(self.win._show_result, instance_id, res.ok,
                              ("Dev-mode auto-restart: " + res.message) if res.ok
                              else ("Dev-mode restart failed: " + res.message))

            threading.Thread(target=_work, daemon=True).start()

        for instance_id, watch in list(self.win._dev_watches.items()):
            try:
                watch["controller"].check(lambda: _fire(instance_id))
            except Exception:
                pass
        return True


    def _devmode_stop(self, instance_id: str, announce: bool = False) -> None:
        watches = self.win._dev_watches or {}
        watch = watches.pop(instance_id, None)
        if watch is not None:
            for monitor in watch.get("monitors", []):
                try:
                    monitor.cancel()
                except Exception:
                    pass
        if not watches and getattr(self, "_devmode_timer_id", 0):
            try:
                GLib.source_remove(self.win._devmode_timer_id)
            except Exception:
                pass
            self.win._devmode_timer_id = 0
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_devmode_state(False, "")
            except Exception:
                pass
        if announce:
            self.win.toast("Dev Mode off (instance left running)")


    def _sync_devmode_toggle(self, instance_id: str, on: bool,
                             note: str = "") -> bool:
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_devmode_state(on, note)
            except Exception:
                pass
        return False

    # --------------------------------------------------- test runner


    def _test_run_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            module, target = self.win.detail.test_fields()
        except Exception:
            module, target = "", ""
        module, target = (module or "").strip(), (target or "").strip()
        if not module:
            self.win.toast("Enter the module technical name to test")
            return
        if not target:
            target = f"{(inst.primary_db or 'odoo').strip()}_test"
        if target == (inst.primary_db or "").strip():
            self.win.toast("Refusing to run tests on the PRIMARY database — "
                       "pick a disposable test database")
            return
        self.win._select_row(instance_id)

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()
            dlg, append, done = self.win._progress_dialog(
                f"Tests: {module} on {target}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.run_module_tests(
                    inst, target, module, progress_cb=_feed)
                summary = (res.data or {}).get("summary", {})
                status = summary.get("status", "?")
                GLib.idle_add(done, res.ok and status == "passed",
                              f"{res.message} | tests: {summary.get('ran', '?')} "
                              f"ran, status={status}")
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

            threading.Thread(target=_work, daemon=True).start()

        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {target} "
               f"-i/-u {module} --test-enable --stop-after-init")
        # Honest command preview: -i vs -u depends on target existence at run
        # time (missing → create+install, existing → update+test).
        self.win.module_ops._confirm_command(
            f"Run {module} tests on '{target}'?",
            f"{cmd}\n\nTarget is disposable (never the primary). Missing DBs "
            "are created+installed (-i); existing ones are updated+tested (-u).",
            "Run tests", _go)


