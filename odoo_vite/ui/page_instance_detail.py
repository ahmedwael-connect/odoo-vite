"""Instance detail page (Sprint 3 base + Sprint 4 databases/remove).

Sections: header + actions, live stats, auto-update editor, databases
(selector + Set Primary + Switch Now + tracked list + Discover + manual
add), danger zone (Remove). Actions route through on_action(action,
instance_id, payload) owned by MainWindow. No business logic here.
"""

import webbrowser

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False

from odoo_vite.core.adopt import check_enterprise_match  # noqa: E402
from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402

OTHER_LABEL = "Other… (type below)"


class InstanceDetailPage(Gtk.Box):
    def __init__(self, on_action=None, on_new_instance=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._on_action = on_action
        self.instance_id: str | None = None

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        if HAS_ADW:
            empty = Adw.StatusPage()
            empty.set_icon_name("computer-symbolic")
            empty.set_title("Odoo Vite")
            empty.set_description(
                "Select an instance in the sidebar or create a new one.")
        else:
            empty = Gtk.Label(label="Select an instance in the sidebar.")
        self.stack.add_named(empty, "empty")

        self.content = self._build_content()
        self.stack.add_named(self.content, "detail")
        self.stack.set_visible_child_name("empty")

    # ------------------------------------------------------------------ build
    def _build_content(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        self.lbl_name = Gtk.Label(xalign=0)
        self.lbl_name.add_css_class("title-1")
        box.append(self.lbl_name)
        self.lbl_sub = Gtk.Label(xalign=0)
        self.lbl_sub.add_css_class("dim-label")
        box.append(self.lbl_sub)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_start = Gtk.Button(label="Start")
        self.btn_start.add_css_class("suggested-action")
        self.btn_start.connect("clicked", self._emit, "start", None)
        btn_row.append(self.btn_start)
        self.btn_stop = Gtk.Button(label="Stop")
        self.btn_stop.connect("clicked", self._emit, "stop", None)
        btn_row.append(self.btn_stop)
        self.btn_restart = Gtk.Button(label="Restart")
        self.btn_restart.connect("clicked", self._emit, "restart", None)
        btn_row.append(self.btn_restart)
        self.btn_browser = Gtk.Button(label="Open in browser")
        self.btn_browser.connect("clicked", self._on_open_browser)
        btn_row.append(self.btn_browser)
        box.append(btn_row)

        self.lbl_error = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.lbl_error.add_css_class("error")
        box.append(self.lbl_error)

        self.btn_repair = Gtk.Button(label="Repair venv (install setuptools/wheel)")
        self.btn_repair.set_tooltip_text(
            "Fixes 'No module named pkg_resources' on instances provisioned "
            "before the packaging fix — no re-provisioning needed")
        self.btn_repair.connect("clicked", self._emit, "repair", None)
        self.btn_repair.set_visible(False)
        box.append(self.btn_repair)

        # ------------------------------------------- H.2/H.5 badges
        self.security_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_security = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.lbl_security.add_css_class("warning")
        self.security_box.append(self.lbl_security)
        self.btn_secure = Gtk.Button(label="Move to keyring now")
        self.btn_secure.connect("clicked", self._on_secure_clicked)
        self.security_box.append(self.btn_secure)
        box.append(self.security_box)

        self.lbl_enterprise = Gtk.Label(xalign=0, wrap=True)
        box.append(self.lbl_enterprise)

        stats_title = Gtk.Label(label="Stats", xalign=0)
        stats_title.add_css_class("heading")
        box.append(stats_title)
        self.stats_list = Gtk.ListBox()
        self.stats_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.stats_list)
        self._stat_rows: dict[str, Gtk.Label] = {}
        for key, caption in (("pid", "PID"), ("port", "Port"),
                             ("cpu", "CPU"), ("memory", "Memory"),
                             ("db", "Primary database"),
                             ("mode", "Provisioning mode"),
                             ("db_created", "Database created")):
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            hbox.append(Gtk.Label(label=caption, xalign=0, hexpand=True))
            val = Gtk.Label(xalign=1)
            val.add_css_class("dim-label")
            hbox.append(val)
            row.set_child(hbox)
            self.stats_list.append(row)
            self._stat_rows[key] = val

        # ------------------------------------------------- environment
        env_title = Gtk.Label(label="Python environment", xalign=0)
        env_title.add_css_class("heading")
        box.append(env_title)
        self.lbl_venv = Gtk.Label(xalign=0, wrap=True)
        self.lbl_venv.add_css_class("dim-label")
        box.append(self.lbl_venv)
        self.venv_edit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_venv = Gtk.Entry(hexpand=True,
                                    placeholder_text="/path/to/venv (must contain bin/python)")
        self.venv_edit_row.append(self.entry_venv)
        btn_venv_save = Gtk.Button(label="Save")
        btn_venv_save.connect("clicked", self._on_save_venv)
        self.venv_edit_row.append(btn_venv_save)
        box.append(self.venv_edit_row)
        self.lbl_venv_hint = Gtk.Label(xalign=0)
        self.lbl_venv_hint.add_css_class("dim-label")
        box.append(self.lbl_venv_hint)

        au_title = Gtk.Label(label="Auto-update modules on start", xalign=0)
        au_title.add_css_class("heading")
        box.append(au_title)
        au_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_modules = Gtk.Entry(hexpand=True,
                                       placeholder_text="e.g. sale, stock (comma-separated)")
        au_row.append(self.entry_modules)
        btn_save = Gtk.Button(label="Save")
        btn_save.connect("clicked", self._on_save_modules)
        au_row.append(btn_save)
        box.append(au_row)
        self.lbl_modules_hint = Gtk.Label(xalign=0)
        self.lbl_modules_hint.add_css_class("dim-label")
        box.append(self.lbl_modules_hint)

        # ------------------------------------------------------- databases
        db_title = Gtk.Label(label="Databases", xalign=0)
        db_title.add_css_class("heading")
        box.append(db_title)

        switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.db_dropdown = Gtk.DropDown()
        self.db_dropdown.set_hexpand(True)
        self.db_dropdown.connect("notify::selected-item",
                                 self._on_dropdown_changed)
        switch_row.append(self.db_dropdown)
        self.btn_set_primary = Gtk.Button(label="Set as Primary")
        self.btn_set_primary.set_tooltip_text(
            "Use for future starts (no restart, instant)")
        self.btn_set_primary.connect("clicked", self._on_set_primary)
        switch_row.append(self.btn_set_primary)
        self.btn_switch = Gtk.Button(label="Switch Now")
        self.btn_switch.set_tooltip_text(
            "Restart the running instance on this database")
        self.btn_switch.connect("clicked", self._on_switch_now)
        switch_row.append(self.btn_switch)
        box.append(switch_row)

        self.other_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.other_row.append(Gtk.Label(label="Database name:"))
        self.entry_other_db = Gtk.Entry(hexpand=True, placeholder_text="my_other_db")
        self.other_row.append(self.entry_other_db)
        self.other_row.set_visible(False)
        box.append(self.other_row)

        self.tracked_list = Gtk.ListBox()
        self.tracked_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.tracked_list)

        manage_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_discover = Gtk.Button(label="Discover databases")
        self.btn_discover.set_tooltip_text(
            "Find Postgres databases for this db user")
        self.btn_discover.connect("clicked", self._emit, "discover", None)
        manage_row.append(self.btn_discover)
        self.entry_manual_db = Gtk.Entry(hexpand=True, placeholder_text="Add by name…")
        manage_row.append(self.entry_manual_db)
        btn_add = Gtk.Button(label="Track")
        btn_add.connect("clicked", self._on_track_manual)
        manage_row.append(btn_add)
        box.append(manage_row)

        ops_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_refresh_dbs = Gtk.Button(label="Refresh states")
        self.btn_refresh_dbs.set_tooltip_text("Re-query Postgres for every tracked DB")
        self.btn_refresh_dbs.connect("clicked", self._emit, "refresh-states", None)
        ops_row.append(self.btn_refresh_dbs)
        self.btn_restore = Gtk.Button(label="Restore…")
        self.btn_restore.set_tooltip_text("Restore a pg_dump file into a database")
        self.btn_restore.connect("clicked", self._emit, "restore", None)
        ops_row.append(self.btn_restore)
        self.btn_validate = Gtk.Button(label="Validate config")
        self.btn_validate.set_tooltip_text(
            "Compare odoo.conf db_* settings against live Postgres")
        self.btn_validate.connect("clicked", self._emit, "validate", None)
        ops_row.append(self.btn_validate)
        box.append(ops_row)

        # ------------------------------------------------------ danger zone
        dz_title = Gtk.Label(label="Danger zone", xalign=0)
        dz_title.add_css_class("heading")
        box.append(dz_title)
        self.btn_remove = Gtk.Button(label="Remove instance…")
        self.btn_remove.add_css_class("destructive-action")
        self.btn_remove.connect("clicked", self._emit, "remove", None)
        box.append(self.btn_remove)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(box)
        return scrolled

    # ---------------------------------------------------------------- actions
    def _emit(self, _btn: Gtk.Button, action: str, payload) -> None:
        if self._on_action is not None and self.instance_id is not None:
            self._on_action(action, self.instance_id, payload)

    def _on_open_browser(self, _btn: Gtk.Button) -> None:
        inst = get_instance(self.instance_id) if self.instance_id else None
        if inst is None:
            return
        url = f"http://localhost:{inst.port}"
        try:
            if hasattr(Gtk, "UriLauncher"):
                launcher = Gtk.UriLauncher(uri=url)  # type: ignore[attr-defined]
                launcher.launch(None, None, None)
                return
        except Exception:
            pass
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _on_save_modules(self, _btn: Gtk.Button) -> None:
        if self.instance_id is None:
            return
        raw = self.entry_modules.get_text() or ""
        modules = [m.strip() for m in raw.split(",") if m.strip()]
        res = update_instance(self.instance_id, auto_update_modules=modules)
        if res.ok:
            self.lbl_modules_hint.set_text(
                "Saved — applied with -u on every Start/Restart."
                if modules else "Saved — no modules will auto-update.")
        else:
            self.lbl_modules_hint.set_text(f"Save failed: {res.message}")

    def _on_save_venv(self, _btn: Gtk.Button) -> None:
        """H-B2: adopted instances can (re)point their Python environment."""
        import os

        if self.instance_id is None:
            return
        path = (self.entry_venv.get_text() or "").strip()
        if path and not os.path.isfile(os.path.join(path, "bin", "python")):
            self.lbl_venv_hint.set_text(
                f"Not a virtualenv: no bin/python under {path}")
            return
        res = update_instance(self.instance_id, venv_path=path)
        if res.ok:
            self.lbl_venv.set_text(path or "— (not set)")
            self.lbl_venv_hint.set_text("Saved.")
        else:
            self.lbl_venv_hint.set_text(f"Save failed: {res.message}")

    def show_repair_option(self, show: bool) -> None:
        """H-B1: offer venv repair after a pkg_resources-pattern start failure."""
        self.btn_repair.set_visible(show)

    def _on_secure_clicked(self, _btn: Gtk.Button) -> None:
        """H.2 one-click sweep: move this instance's password to the keyring."""
        import threading

        from odoo_vite.core.registry import migrate_password_to_keyring

        if self.instance_id is None:
            return
        self.btn_secure.set_sensitive(False)
        instance_id = self.instance_id

        def _work() -> None:
            res = migrate_password_to_keyring(instance_id)
            GLib.idle_add(self._on_secured, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _on_secured(self, ok: bool, message: str) -> bool:
        self.btn_secure.set_sensitive(True)
        if ok:
            self.show_instance(self.instance_id)  # hides the badge
            if self._on_action is not None and self.instance_id is not None:
                self._on_action("secured", self.instance_id, message)
        else:
            self.show_error(f"Could not secure password: {message}")
        return False

    # -------------------------------------------------------------- databases
    def _selected_db(self) -> str:
        item = self.db_dropdown.get_selected_item()
        text = item.get_string() if item is not None else ""
        if text == OTHER_LABEL:
            return (self.entry_other_db.get_text() or "").strip()
        return (text or "").strip()

    def _on_dropdown_changed(self, _dd, _pspec) -> None:
        item = self.db_dropdown.get_selected_item()
        text = item.get_string() if item is not None else ""
        self.other_row.set_visible(text == OTHER_LABEL)

    def _on_set_primary(self, _btn: Gtk.Button) -> None:
        self._emit(_btn, "set-primary", self._selected_db())

    def _on_switch_now(self, _btn: Gtk.Button) -> None:
        self._emit(_btn, "switch", self._selected_db())

    def _on_track_manual(self, _btn: Gtk.Button) -> None:
        name = (self.entry_manual_db.get_text() or "").strip()
        if name:
            self.entry_manual_db.set_text("")
            self._emit(_btn, "track", name)

    def _on_untrack(self, _btn: Gtk.Button, db_name: str) -> None:
        self._emit(_btn, "untrack", db_name)

    def refresh_databases(self) -> None:
        """Rebuild the dropdown + tracked list from the registry row.

        Live state (size/version/initialized) arrives separately via
        set_db_states(), filled by a background fetch owned by MainWindow.
        """
        while True:
            row = self.tracked_list.get_row_at_index(0)
            if row is None:
                break
            self.tracked_list.remove(row)
        self._db_rows = {}
        inst = get_instance(self.instance_id) if self.instance_id else None
        tracked = list(inst.tracked_dbs) if inst and inst.tracked_dbs else []
        primary = inst.primary_db if inst else ""
        model = Gtk.StringList.new(tracked + [OTHER_LABEL])
        self.db_dropdown.set_model(model)
        try:
            self.db_dropdown.set_selected(tracked.index(primary))
        except ValueError:
            self.db_dropdown.set_selected(0 if tracked else len(tracked))
        self.other_row.set_visible(False)
        self._stat_rows["db"].set_text(primary or "—")
        for db_name in tracked:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            marker = "  ★ primary" if db_name == primary else ""
            vbox.append(Gtk.Label(label=f"{db_name}{marker}", xalign=0))
            state_lbl = Gtk.Label(label="state: loading…", xalign=0)
            state_lbl.add_css_class("dim-label")
            vbox.append(state_lbl)
            hbox.append(vbox)
            btn_init = Gtk.Button(label="Init")
            btn_init.set_tooltip_text("Initialize this database (-i base)")
            btn_init.connect("clicked", self._emit, "init-db", db_name)
            btn_init.set_visible(False)
            hbox.append(btn_init)
            btn_backup = Gtk.Button(label="Backup")
            btn_backup.connect("clicked", self._emit, "backup-db", db_name)
            hbox.append(btn_backup)
            if db_name != primary:
                btn_drop = Gtk.Button(label="Drop")
                btn_drop.add_css_class("destructive-action")
                btn_drop.connect("clicked", self._emit, "drop-db", db_name)
                hbox.append(btn_drop)
            btn = Gtk.Button(label="Untrack")
            btn.connect("clicked", self._on_untrack, db_name)
            hbox.append(btn)
            row.set_child(hbox)
            self.tracked_list.append(row)
            self._db_rows[db_name] = {"state": state_lbl, "init": btn_init}

    def set_db_states(self, states: dict, instance_version: str = "") -> None:
        """Fill live state into tracked rows (B.1 table + H.5-style badges)."""
        for db_name, widgets in getattr(self, "_db_rows", {}).items():
            info = (states or {}).get(db_name) or {}
            parts = []
            if info.get("size"):
                parts.append(info["size"])
            if info.get("odoo_version"):
                parts.append(f"v{info['odoo_version']}")
            state_txt = "initialized" if info.get("initialized") else (
                "exists, not initialized" if info.get("exists", True) else "missing")
            parts.append(state_txt)
            widgets["state"].set_text(" · ".join(parts) if parts else "state: unknown")
            mismatch = (info.get("initialized") and info.get("odoo_major")
                        and instance_version
                        and info["odoo_major"] != instance_version)
            if mismatch:
                widgets["state"].set_text(
                    widgets["state"].get_text()
                    + f"  ⚠ v{info['odoo_major']} ≠ instance v{instance_version}")
                widgets["state"].add_css_class("warning")
            else:
                widgets["state"].remove_css_class("warning")
            widgets["init"].set_visible(not info.get("initialized"))

    # ---------------------------------------------------------------- render
    def show_instance(self, instance_id: str | None) -> None:
        self.instance_id = instance_id
        self.lbl_error.set_text("")
        if instance_id is None:
            self.stack.set_visible_child_name("empty")
            return
        inst = get_instance(instance_id)
        if inst is None:
            self.stack.set_visible_child_name("empty")
            return
        self.lbl_name.set_text(inst.name)
        self._stat_rows["db_created"].set_text(
            "yes" if inst.db_created else "not yet (first Start will create it)")
        self._stat_rows["mode"].set_text(
            "Managed (least-privilege)" if (inst.provisioning_mode or "developer") == "managed"
            else "Developer (CREATEDB role)")
        self.show_repair_option(False)
        self.lbl_venv.set_text(inst.venv_path or "— (not set)")
        adopted = (inst.mode or "managed") == "adopted"
        self.venv_edit_row.set_visible(adopted)
        if adopted and inst.venv_path:
            self.entry_venv.set_text(inst.venv_path)
        self.lbl_venv_hint.set_text(
            "Set the virtualenv this adopted install runs with." if adopted else "")
        self.entry_modules.set_text(", ".join(inst.auto_update_modules or []))
        self.lbl_modules_hint.set_text("")
        # H.2 sweep badge: persistent warning while plaintext is stored.
        if inst.password_storage == "plaintext":
            self.lbl_security.set_text(
                "⚠ Database password stored in PLAINTEXT — click to secure it "
                "in the OS keyring.")
            self.security_box.set_visible(True)
        else:
            self.security_box.set_visible(False)
        # H.5 enterprise badge: persistent on mismatch, quiet otherwise.
        self._refresh_enterprise_badge(inst)
        self.stack.set_visible_child_name("detail")
        self.refresh_databases()
        self.update_status({
            "id": inst.id, "name": inst.name, "status": inst.status,
            "pid": inst.pid, "port": inst.port, "version": inst.version,
            "cpu_percent": None, "memory_mb": None})

    def update_status(self, status: dict) -> None:
        """In-place live update from a get_statuses() entry."""
        if self.instance_id != status.get("id"):
            return
        state = (status.get("status") or "stopped").lower()
        self.lbl_sub.set_text(
            f"{status.get('version', '')}  ·  {state.capitalize()}")
        running = state == "running"
        self.btn_start.set_visible(not running)
        self.btn_stop.set_visible(running)
        self.btn_restart.set_visible(running)
        self.btn_browser.set_visible(running)
        self._stat_rows["pid"].set_text(
            str(status["pid"]) if status.get("pid") else "—")
        self._stat_rows["port"].set_text(str(status.get("port", "—")))
        cpu = status.get("cpu_percent")
        self._stat_rows["cpu"].set_text(
            f"{cpu:.1f}%" if cpu is not None else "—")
        mem = status.get("memory_mb")
        self._stat_rows["memory"].set_text(
            f"{mem:.0f} MB" if mem is not None else "—")

    def _refresh_enterprise_badge(self, inst) -> None:
        if not inst.enterprise_path:
            self.lbl_enterprise.set_visible(False)
            return
        try:
            info = check_enterprise_match(inst.version, inst.enterprise_path)
        except Exception:
            info = {"match": None, "enterprise_major": ""}
        self.lbl_enterprise.set_visible(True)
        if info.get("match") is True:
            self.lbl_enterprise.set_text(
                f"Enterprise addons {info.get('enterprise_major')} ✓ "
                f"match Odoo {inst.version}.")
        elif info.get("match") is False:
            self.lbl_enterprise.set_text(
                f"⚠ Enterprise addons {info.get('enterprise_major')} do NOT "
                f"match Odoo {inst.version} — verify compatibility.")
        else:
            self.lbl_enterprise.set_text(
                "Enterprise addons set, version unknown — verify compatibility.")

    def show_error(self, message: str) -> None:
        self.lbl_error.set_text(message)
