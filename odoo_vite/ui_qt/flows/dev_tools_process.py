"""DevTools process flows (PSQ-8.3b): faithful port of GTK dev_tools_process.

Shell REPL (core OdooShell owns the PTY — Qt only polls drain_output on
a QTimer into the page, same poll pattern as GTK), Dev Mode Watch
(QFileSystemWatcher per directory + core DebounceController; filtering
is devwatch.should_watch verbatim, dotfile-parent lesson included),
module test runner (progress dialog, never-primary guard).
"""

from PySide6.QtCore import QObject, QFileSystemWatcher, QTimer, Signal

from odoo_vite.core import devwatch, odoo_shell, process_manager
from odoo_vite.core.registry import get_instance
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm
from odoo_vite.ui_qt.widgets.progress_dialog import ProgressDialog
from odoo_vite.ui_qt.workers import run_in_background


class DevToolsProcessFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    shellOutput = Signal(str, list)  # (instance_id, lines)
    shellStatus = Signal(str, str)  # (instance_id, text)
    devmodeState = Signal(str, bool, str)  # (instance_id, on, note)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._shells: dict[str, object] = {}
        self._watches: dict[str, dict] = {}
        self._shell_timer = QTimer(self)
        self._shell_timer.setInterval(500)
        self._shell_timer.timeout.connect(self._shell_poll_tick)
        self._devmode_timer = QTimer(self)
        self._devmode_timer.setInterval(250)
        self._devmode_timer.timeout.connect(self._devmode_tick)

    # ------------------------------------------------------------ shell

    def shell_start(self, instance_id: str, db_name: str = "") -> None:
        old = self._shells.pop(instance_id, None)
        try:
            if old is not None:
                old.stop()
        except Exception:
            pass
        inst = get_instance(instance_id)
        if inst is None:
            return
        session = odoo_shell.OdooShell()

        def _work():
            return session.start(inst, db_name or "")

        def _done(ok: bool, message: str, _data: dict) -> None:
            if ok:
                self._shells[instance_id] = session
                if not self._shell_timer.isActive():
                    self._shell_timer.start()
            self.shellStatus.emit(instance_id, message)
            self.message.emit(message)

        run_in_background(self, _work, _done)

    def shell_send(self, instance_id: str, text: str) -> None:
        session = self._shells.get(instance_id)
        if session is None:
            self.message.emit("Shell is not running")
            return
        res = session.send_line(text)
        if not res.ok:
            self.message.emit(res.message)

    def shell_stop(self, instance_id: str) -> None:
        session = self._shells.pop(instance_id, None)
        if session is None:
            return

        def _work():
            return session.stop()

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.shellStatus.emit(instance_id, "Shell stopped.")
            if not self._shells and self._shell_timer.isActive():
                self._shell_timer.stop()

        run_in_background(self, _work, _done)

    def _shell_poll_tick(self) -> None:
        if not self._shells:
            self._shell_timer.stop()
            return
        for instance_id, session in list(self._shells.items()):
            try:
                lines = session.drain_output()
            except Exception:
                lines = []
            if lines:
                self.shellOutput.emit(instance_id, lines)
            try:
                alive = session.running
            except Exception:
                alive = False
            if not alive:
                self._shells.pop(instance_id, None)
                self.shellStatus.emit(instance_id, "Shell exited.")

    # ------------------------------------------------------------ devmode

    def devmode(self, instance_id: str, on: bool) -> None:
        if on:
            self._devmode_start(instance_id)
        else:
            self._devmode_stop(instance_id, announce=True)

    def _collect_watch_dirs(self, roots: list) -> list:
        """All watched dirs under roots (SKIP_DIRS pruned, same as GTK).

        QFileSystemWatcher is NOT recursive (unlike Gio) — every dir is
        added explicitly. should_watch decides per FILE at event time.
        """
        import os

        dirs = []
        for root in roots:
            for dirpath, dirnames, _filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in devwatch.SKIP_DIRS]
                dirs.append(dirpath)
        return dirs

    def _devmode_start(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        roots = devwatch.watch_roots(inst)
        if not roots:
            self.message.emit("Dev Mode needs at least one addons folder")
            self.devmodeState.emit(instance_id, False, "no folders")
            return
        try:
            dirs = self._collect_watch_dirs(roots)
        except Exception as exc:
            self.message.emit(f"Dev Mode failed to watch: {exc}")
            self.devmodeState.emit(instance_id, False, "watch failed")
            return
        watcher = QFileSystemWatcher(self)
        if dirs:
            watcher.addPaths(dirs)
        controller = devwatch.DebounceController()
        watcher.directoryChanged.connect(
            lambda path: self._on_watch_event(instance_id, path,
                                              controller, watcher))
        self._watches[instance_id] = {
            "watcher": watcher, "controller": controller, "roots": roots}
        if not self._devmode_timer.isActive():
            self._devmode_timer.start()
        self.devmodeState.emit(instance_id, True,
                               f"watching {len(roots)} folder(s)")
        self.message.emit("Dev Mode on — saving watched files restarts")

    def _on_watch_event(self, instance_id: str, path: str, controller,
                        watcher: QFileSystemWatcher) -> None:
        import os

        # New subdirectories join the watch (GTK CREATED-parity).
        try:
            if os.path.isdir(path):
                for dirpath, dirnames, _f in os.walk(path):
                    dirnames[:] = [d for d in dirnames
                                   if d not in devwatch.SKIP_DIRS]
                    try:
                        watcher.addPath(dirpath)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            changed = [os.path.join(path, name)
                       for name in os.listdir(path)]
        except OSError:
            changed = [path]
        for candidate in changed:
            try:
                if devwatch.should_watch(candidate):
                    controller.feed()
                    break
            except Exception:
                pass

    def _devmode_tick(self) -> None:
        if not self._watches:
            self._devmode_timer.stop()
            return
        for instance_id, watch in list(self._watches.items()):
            try:
                fired = watch["controller"].check(
                    lambda: self._devmode_fire(instance_id))
            except Exception:
                fired = False
            _ = fired

    def _devmode_fire(self, instance_id: str) -> None:
        self.message.emit("Dev-mode file change — restarting")

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(
                ("Dev-mode auto-restart: " + message) if ok
                else ("Dev-mode restart failed: " + message))

        run_in_background(self, process_manager.restart_instance, _done,
                          instance_id)

    def _devmode_stop(self, instance_id: str, announce: bool = False) -> None:
        watch = self._watches.pop(instance_id, None)
        if watch is not None:
            try:
                watch["watcher"].deleteLater()
            except Exception:
                pass
        if not self._watches and self._devmode_timer.isActive():
            self._devmode_timer.stop()
        self.devmodeState.emit(instance_id, False, "")
        if announce:
            self.message.emit("Dev Mode off")

    # ------------------------------------------------------------ tests

    def test_run(self, instance_id: str, module: str, target: str) -> None:
        from odoo_vite.core import module_manager

        inst = get_instance(instance_id)
        if inst is None:
            return
        module, target = (module or "").strip(), (target or "").strip()
        if not module:
            self.message.emit("Enter the module technical name to test")
            return
        if not target:
            target = f"{(inst.primary_db or 'odoo').strip()}_test"
        if target == (inst.primary_db or "").strip():
            self.message.emit("Refusing to run tests on the PRIMARY "
                              "database — pick a disposable test database")
            return
        parent = self.parent()
        from PySide6.QtWidgets import QWidget
        if not ask_confirm(
                parent if isinstance(parent, QWidget) else None,
                f"Run {module} tests on '{target}'?",
                "Tests create/modify/destroy data — never the primary DB.",
                "Run tests"):
            return
        dlg = ProgressDialog(
            parent if isinstance(parent, QWidget) else None,
            f"Tests: {module} on {target}")
        dlg.show()

        def _work():
            return module_manager.run_module_tests(
                inst, target, module,
                progress_cb=dlg.request_append.emit,
                cancel=dlg.cancel_event.is_set)

        def _done(ok: bool, message: str, _data: dict) -> None:
            dlg.request_done.emit(ok, message)
            self.message.emit(message)

        run_in_background(self, _work, _done)
