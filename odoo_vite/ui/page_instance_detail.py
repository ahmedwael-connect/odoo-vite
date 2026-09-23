"""Instance detail page (Sprint 3 base + Sprint 4 databases/remove).

Sections: header + actions, live stats, auto-update editor, databases
(selector + Set Primary + Switch Now + tracked list + Discover + manual
add), danger zone (Remove). Actions route through on_action(action,
instance_id, payload) owned by MainWindow. No business logic here.
"""

import webbrowser

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

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
        self._running = False

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
    def _tab_box(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(12)
        box.set_margin_bottom(16)
        return box

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tab_stack = Gtk.Stack()
        self.tab_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.tab_stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(6)
        outer.append(switcher)
        self.tab_stack.add_titled(self._build_overview(), "overview", "Overview")
        self.tab_stack.add_titled(self._build_databases(), "databases", "Databases")
        self.tab_stack.add_titled(self._build_modules(), "modules", "Modules")
        self.tab_stack.add_titled(self._build_logs(), "logs", "Logs")
        self.tab_stack.add_titled(self._build_configuration(), "configuration",
                                  "Configuration")
        outer.append(self.tab_stack)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(outer)
        return scrolled

    def _build_overview(self) -> Gtk.Widget:
        box = self._tab_box()
        self.lbl_name = Gtk.Label(xalign=0)
        self.lbl_name.add_css_class("title-1")
        box.append(self.lbl_name)
        self.lbl_desc = Gtk.Label(xalign=0, wrap=True)
        self.lbl_desc.add_css_class("dim-label")
        box.append(self.lbl_desc)
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

        dz_title = Gtk.Label(label="Danger zone", xalign=0)
        dz_title.add_css_class("heading")
        box.append(dz_title)
        self.btn_remove = Gtk.Button(label="Remove instance…")
        self.btn_remove.add_css_class("destructive-action")
        self.btn_remove.connect("clicked", self._emit, "remove", None)
        box.append(self.btn_remove)
        return box

    def _build_databases(self) -> Gtk.Widget:
        box = self._tab_box()
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
        return box

    # ---------------------------------------------------------------- modules
    def _build_modules(self) -> Gtk.Widget:
        box = self._tab_box()
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Modules", xalign=0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        self.lbl_mod_db = Gtk.Label(xalign=1)
        self.lbl_mod_db.add_css_class("dim-label")
        head.append(self.lbl_mod_db)
        box.append(head)

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_mod_search = Gtk.SearchEntry(
            placeholder_text="Filter by name or summary…", hexpand=True)
        self.entry_mod_search.connect("search-changed",
                                      lambda _e: self._render_module_rows())
        filter_row.append(self.entry_mod_search)
        self.mod_state_filter = Gtk.DropDown(model=Gtk.StringList.new(
            ["All", "Installed", "Upgradeable", "Installable"]))
        self.mod_state_filter.connect("notify::selected",
                                      lambda *_a: self._render_module_rows())
        filter_row.append(self.mod_state_filter)
        self.btn_mod_refresh = Gtk.Button(label="Refresh")
        self.btn_mod_refresh.connect("clicked", self._emit, "mod-refresh", None)
        filter_row.append(self.btn_mod_refresh)
        box.append(filter_row)

        self.modules_list = Gtk.ListBox()
        self.modules_list.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        box.append(self.modules_list)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_mod_install = Gtk.Button(label="Install Selected")
        self.btn_mod_install.add_css_class("suggested-action")
        self.btn_mod_install.connect("clicked", self._on_mod_install_selected)
        btn_row.append(self.btn_mod_install)
        self.btn_mod_update = Gtk.Button(label="Update Selected")
        self.btn_mod_update.connect("clicked", self._on_mod_update_selected)
        btn_row.append(self.btn_mod_update)
        self.btn_mod_code = Gtk.Button(label="Update Code…")
        self.btn_mod_code.set_tooltip_text(
            "git pull community + pip install + -u (instance must be stopped)")
        self.btn_mod_code.connect("clicked", self._emit, "mod-update-code", None)
        btn_row.append(self.btn_mod_code)
        self.btn_mod_deps = Gtk.Button(label="Dependencies…")
        self.btn_mod_deps.connect("clicked", self._emit, "mod-deps", None)
        btn_row.append(self.btn_mod_deps)
        self.btn_mod_scaffold = Gtk.Button(label="New Module…")
        self.btn_mod_scaffold.connect("clicked", self._emit, "mod-scaffold", None)
        btn_row.append(self.btn_mod_scaffold)
        box.append(btn_row)

        self._modules_cache: list = []
        self._diff_cache: dict = {}
        return box

    def _selected_module_names(self, states=None) -> list:
        names = []
        for row in self.modules_list.get_selected_rows():
            name = getattr(row, "module_name", None)
            if not name:
                continue
            if states is not None:
                st = next((m.get("state") for m in self._modules_cache
                           if m.get("name") == name), None)
                if st not in states:
                    continue
            names.append(name)
        return names

    def _on_mod_install_selected(self, _btn: Gtk.Button) -> None:
        names = self._selected_module_names(states=("uninstalled", "to install"))
        if not names:
            names = self._selected_module_names()
        self._emit(_btn, "mod-install", names)

    def _on_mod_update_selected(self, _btn: Gtk.Button) -> None:
        names = self._selected_module_names()
        self._emit(_btn, "mod-update", names)

    def _mod_state_category(self, mod: dict) -> str:
        state = (mod.get("state") or "").strip()
        if state == "installed":
            try:
                from odoo_vite.core.module_manager import _ver_tuple

                installed = _ver_tuple(mod.get("installed_version") or "")
                available = _ver_tuple(mod.get("available_version") or "")
                latest = _ver_tuple(mod.get("latest_version") or "")
                if available and available > installed:
                    return "Upgradeable"
                if latest and latest > installed:
                    return "Upgradeable"
            except Exception:
                pass
            return "Installed"
        if state in ("to upgrade",):
            return "Upgradeable"
        if state in ("uninstalled", "to install"):
            return "Installable"
        return state.capitalize() or "Unknown"

    def set_modules(self, modules: list, diff: dict | None = None) -> None:
        """Fill the modules tab (window fetches in background)."""
        self._modules_cache = list(modules or [])
        self._diff_cache = dict(diff or {})
        self._render_module_rows()

    def _render_module_rows(self) -> None:
        while True:
            row = self.modules_list.get_row_at_index(0)
            if row is None:
                break
            self.modules_list.remove(row)
        needle = ""
        try:
            needle = (self.entry_mod_search.get_text() or "").strip().lower()
        except Exception:
            pass
        try:
            filt_item = self.mod_state_filter.get_selected_item()
            filt = filt_item.get_string() if filt_item is not None else "All"
        except Exception:
            filt = "All"
        for mod in self._modules_cache:
            name = mod.get("name", "")
            if needle and needle not in name.lower() and needle not in str(
                    mod.get("summary", "")).lower():
                continue
            if filt != "All" and self._mod_state_category(mod) != filt:
                continue
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            title = Gtk.Label(label=f"{name}  ({self._mod_state_category(mod)})",
                              xalign=0)
            vbox.append(title)
            sub = Gtk.Label(
                label=f"{mod.get('installed_version') or mod.get('available_version') or ''}"
                      f"  ·  {mod.get('summary', '')}"[:160], xalign=0)
            sub.add_css_class("dim-label")
            vbox.append(sub)
            info = (self._diff_cache or {}).get(name)
            if info and info.get("status") not in (None, "in-sync"):
                badge = Gtk.Label(label=f"⚠ {info.get('note', '')}", xalign=0, wrap=True)
                badge.add_css_class("warning")
                vbox.append(badge)
            hbox.append(vbox)
            if (mod.get("state") or "") == "installed":
                btn = Gtk.Button(label="Uninstall")
                btn.add_css_class("destructive-action")
                btn.connect("clicked", self._emit, "mod-uninstall", name)
                hbox.append(btn)
            else:
                btn = Gtk.Button(label="Install")
                btn.connect("clicked", self._emit, "mod-install", [name])
                hbox.append(btn)
            row.set_child(hbox)
            row.module_name = name  # type: ignore[attr-defined]
            self.modules_list.append(row)


    # ------------------------------------------------------- configuration
    COMMON_KEYS = ["db_host", "db_port", "db_user", "xmlrpc_port", "logfile"]
    LOG_LEVELS = ["info", "debug", "debug_sql", "warning", "error", "critical"]

    def _build_configuration(self) -> Gtk.Widget:
        box = self._tab_box()
        title = Gtk.Label(label="Configuration (odoo.conf)", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        self.lbl_conf_path = Gtk.Label(xalign=0)
        self.lbl_conf_path.add_css_class("dim-label")
        box.append(self.lbl_conf_path)

        self.conf_table = Gtk.ListBox()
        self.conf_table.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.conf_table)

        form_title = Gtk.Label(label="Edit common keys", xalign=0)
        form_title.add_css_class("heading")
        box.append(form_title)
        self.conf_entries = {}
        for key in self.COMMON_KEYS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=key, xalign=0, width_request=110))
            entry = Gtk.Entry(hexpand=True)
            row.append(entry)
            box.append(row)
            self.conf_entries[key] = entry
        if True:  # addons_path is managed, not typed: read-only + manager link
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label="addons_path", xalign=0, width_request=110))
            self.lbl_addons_ro = Gtk.Label(xalign=0, hexpand=True)
            self.lbl_addons_ro.add_css_class("dim-label")
            # A.1: addons_path is one unbroken string — ellipsize, don't wrap-wide.
            self.lbl_addons_ro.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            self.lbl_addons_ro.set_max_width_chars(60)
            row.append(self.lbl_addons_ro)
            btn_addons = Gtk.Button(label="Manage…")
            btn_addons.connect("clicked", self._emit, "addons-manage", None)
            row.append(btn_addons)
            box.append(row)

        raw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_raw_key = Gtk.Entry(hexpand=True, placeholder_text="raw key")
        raw_row.append(self.entry_raw_key)
        self.entry_raw_value = Gtk.Entry(
            hexpand=True, placeholder_text="value (empty deletes the key)")
        raw_row.append(self.entry_raw_value)
        btn_raw = Gtk.Button(label="Set")
        btn_raw.connect("clicked", self._on_raw_set)
        raw_row.append(btn_raw)
        box.append(raw_row)

        self.lbl_conf_notice = Gtk.Label(xalign=0, wrap=True)
        self.lbl_conf_notice.add_css_class("warning")
        box.append(self.lbl_conf_notice)

        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_conf_save = Gtk.Button(label="Save changes")
        self.btn_conf_save.add_css_class("suggested-action")
        self.btn_conf_save.connect("clicked", self._on_conf_save)
        save_row.append(self.btn_conf_save)
        self.btn_conf_restore = Gtk.Button(label="Restore last backup")
        self.btn_conf_restore.connect("clicked", self._emit, "conf-restore", None)
        save_row.append(self.btn_conf_restore)
        box.append(save_row)

        adv_title = Gtk.Label(label="Advanced", xalign=0)
        adv_title.add_css_class("heading")
        box.append(adv_title)
        self.btn_conf_regen = Gtk.Button(label="Regenerate from registry…")
        self.btn_conf_regen.set_tooltip_text(
            "Rebuild [options] from registry fields (overwrites manual edits)")
        self.btn_conf_regen.connect("clicked", self._emit, "conf-regenerate", None)
        box.append(self.btn_conf_regen)

        meta_title = Gtk.Label(label="Metadata", xalign=0)
        meta_title.add_css_class("heading")
        box.append(meta_title)
        box.append(Gtk.Label(label="Description (registry only, for organization)",
                             xalign=0))
        self.entry_description = Gtk.Entry(hexpand=True)
        box.append(self.entry_description)
        workers_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        workers_row.append(Gtk.Label(label="Workers", xalign=0))
        self.spin_workers = Gtk.SpinButton.new_with_range(0, 64, 1)
        workers_row.append(self.spin_workers)
        self.drop_log_level = Gtk.DropDown(
            model=Gtk.StringList.new(self.LOG_LEVELS))
        workers_row.append(Gtk.Label(label="Log level", xalign=0))
        workers_row.append(self.drop_log_level)
        box.append(workers_row)
        box.append(Gtk.Label(
            label="Workers: 0 = single-process dev mode (the default so far). "
                  "Above 0 needs a free gevent/longpolling port and more RAM; "
                  "cron moves to a dedicated worker. When in doubt, keep 0.",
            xalign=0, wrap=True))
        py_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        py_row.append(Gtk.Label(label="Custom interpreter", xalign=0))
        self.entry_python = Gtk.Entry(
            hexpand=True, placeholder_text="empty = venv's own python")
        py_row.append(self.entry_python)
        btn_py_browse = Gtk.Button(label="Browse…")
        btn_py_browse.connect("clicked", self._on_browse_python)
        py_row.append(btn_py_browse)
        box.append(py_row)
        self.err_python = Gtk.Label(xalign=0)
        self.err_python.add_css_class("error")
        box.append(self.err_python)
        btn_meta_save = Gtk.Button(label="Save metadata")
        btn_meta_save.connect("clicked", self._on_meta_save)
        box.append(btn_meta_save)
        self._conf_options = {}
        self._running = False
        return box

    def refresh_conf(self) -> None:
        """Reload the conf table + editors from disk (local, instant)."""
        from odoo_vite.core import conf_manager

        while True:
            row = self.conf_table.get_row_at_index(0)
            if row is None:
                break
            self.conf_table.remove(row)
        inst = get_instance(self.instance_id) if self.instance_id else None
        if inst is None or not inst.conf_path:
            self.lbl_conf_path.set_text("No conf recorded.")
            self._conf_options = {}
            return
        self.lbl_conf_path.set_text(inst.conf_path)
        res = conf_manager.read_conf(inst.conf_path)
        if not res.ok:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=f"Cannot read conf: {res.message}",
                                    xalign=0))
            self.conf_table.append(row)
            self._conf_options = {}
            return
        self._conf_options = dict(res.data["options"])
        for key, value in res.data["options"].items():
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.append(Gtk.Label(label=key, xalign=0, hexpand=True))
            val = Gtk.Label(label=value, xalign=1, selectable=True)
            val.add_css_class("dim-label")
            # A.1 (Sprint 8): long conf values must not widen the tab.
            val.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            val.set_max_width_chars(40)
            hbox.append(val)
            row.set_child(hbox)
            self.conf_table.append(row)
        for key, entry in self.conf_entries.items():
            entry.set_text(self._conf_options.get(key, ""))
        self.lbl_addons_ro.set_text(self._conf_options.get("addons_path", "—"))
        info = conf_manager.conf_backup_info(inst.conf_path)
        self.btn_conf_restore.set_sensitive(info is not None)
        self.btn_conf_restore.set_tooltip_text(
            f"Restore from {info['path']}" if info else "No backup yet")
        self.entry_description.set_text(inst.description or "")
        self.spin_workers.set_value(inst.workers or 0)
        try:
            self.drop_log_level.set_selected(
                self.LOG_LEVELS.index(inst.log_level or "info"))
        except ValueError:
            self.drop_log_level.set_selected(0)
        self.entry_python.set_text(inst.python_binary or "")

    def _on_conf_save(self, _btn: Gtk.Button) -> None:
        changes = {}
        for key, entry in self.conf_entries.items():
            new = entry.get_text()
            old = self._conf_options.get(key, "")
            if new != old:
                changes[key] = new
        if not changes:
            self.lbl_conf_notice.set_text("No changes to save.")
            return
        self._emit(_btn, "conf-save", changes)

    def _on_raw_set(self, _btn: Gtk.Button) -> None:
        key = (self.entry_raw_key.get_text() or "").strip()
        if not key:
            self.lbl_conf_notice.set_text("Enter a key name first.")
            return
        value = self.entry_raw_value.get_text()
        self.entry_raw_key.set_text("")
        self.entry_raw_value.set_text("")
        self._emit(_btn, "conf-save",
                   {key: (None if value == "" else value)})

    def _on_browse_python(self, _btn: Gtk.Button) -> None:
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title="Select Python interpreter")
            dlg.open(self.get_root(), None, self._on_python_chosen)
        else:
            self.err_python.set_text("File picker unavailable on this GTK.")

    def _on_python_chosen(self, dlg, result) -> None:
        try:
            picked = dlg.open_finish(result)
            path = picked.get_path() if picked else None
        except Exception:
            return
        if path:
            self.entry_python.set_text(path)

    def _on_meta_save(self, _btn: Gtk.Button) -> None:
        import os

        pybin = (self.entry_python.get_text() or "").strip()
        if pybin and not (os.path.isfile(pybin) and os.access(pybin, os.X_OK)):
            self.err_python.set_text(f"Not an executable: {pybin}")
            return
        self.err_python.set_text("")
        item = self.drop_log_level.get_selected_item()
        self._emit(_btn, "meta-save", {
            "description": self.entry_description.get_text(),
            "workers": int(self.spin_workers.get_value_as_int()),
            "log_level": item.get_string() if item is not None else "info",
            "python_binary": pybin,
        })

    # ---------------------------------------------------------------- logs
    LOG_LEVELS_ALL = ["All levels", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    LOG_MODEL_CAP = 5000

    def _build_logs(self) -> Gtk.Widget:
        from gi.repository import Gio as _Gio

        box = self._tab_box()
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Logs", xalign=0, hexpand=True)
        title.add_css_class("heading")
        toolbar.append(title)
        self.btn_log_pause = Gtk.ToggleButton(label="Pause")
        self.btn_log_pause.set_tooltip_text(
            "Freeze auto-scroll (also pauses automatically when you scroll up)")
        self.btn_log_pause.connect("toggled", self._on_log_pause_toggled)
        toolbar.append(self.btn_log_pause)
        self.btn_doctor = Gtk.Button(label="Run Doctor")
        self.btn_doctor.set_tooltip_text("Scan the log for known failure signatures")
        self.btn_doctor.connect("clicked", self._emit, "log-doctor", None)
        toolbar.append(self.btn_doctor)
        self.drop_profile_dur = Gtk.DropDown(
            model=Gtk.StringList.new(["5s", "10s", "30s"]))
        self.drop_profile_dur.set_selected(1)
        toolbar.append(self.drop_profile_dur)
        self.btn_profile = Gtk.Button(label="Profile")
        self.btn_profile.set_tooltip_text(
            "Record a py-spy flame graph of the running process")
        self.btn_profile.connect("clicked", self._on_profile_clicked)
        toolbar.append(self.btn_profile)
        box.append(toolbar)

        self.lbl_log_paused = Gtk.Label(
            label="⏸ paused — scroll to the bottom to resume live follow",
            xalign=0)
        self.lbl_log_paused.add_css_class("warning")
        self.lbl_log_paused.set_visible(False)
        box.append(self.lbl_log_paused)
        self.lbl_log_note = Gtk.Label(xalign=0, wrap=True)
        self.lbl_log_note.add_css_class("dim-label")
        box.append(self.lbl_log_note)

        self.log_store = _Gio.ListStore(item_type=Gtk.StringObject)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_log_row_setup)
        factory.connect("bind", self._on_log_row_bind)
        self.log_view = Gtk.ListView(model=Gtk.NoSelection(model=self.log_store),
                                     factory=factory)
        self.log_scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.log_scrolled.set_min_content_height(220)
        self.log_scrolled.set_child(self.log_view)
        box.append(self.log_scrolled)

        search_title = Gtk.Label(label="Search (full file, streamed)", xalign=0)
        search_title.add_css_class("heading")
        box.append(search_title)
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_log_search = Gtk.SearchEntry(hexpand=True,
                                                placeholder_text="regex pattern…")
        search_row.append(self.entry_log_search)
        self.drop_log_level = Gtk.DropDown(model=Gtk.StringList.new(
            self.LOG_LEVELS_ALL))
        search_row.append(self.drop_log_level)
        self.btn_log_search = Gtk.Button(label="Search")
        self.btn_log_search.connect("clicked", self._emit, "log-search", None)
        search_row.append(self.btn_log_search)
        box.append(search_row)
        self.lbl_search_status = Gtk.Label(xalign=0)
        self.lbl_search_status.add_css_class("dim-label")
        box.append(self.lbl_search_status)
        self.search_results = Gtk.ListBox()
        self.search_results.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.search_results)

        self.doctor_list = Gtk.ListBox()
        self.doctor_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.doctor_list.set_visible(False)
        box.append(self.doctor_list)

        slow_title = Gtk.Label(label="Slow queries (pg_stat_statements)", xalign=0)
        slow_title.add_css_class("heading")
        box.append(slow_title)
        slow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_slow_status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.lbl_slow_status.add_css_class("dim-label")
        slow_row.append(self.lbl_slow_status)
        self.btn_slow_refresh = Gtk.Button(label="Refresh")
        self.btn_slow_refresh.connect("clicked", self._emit, "slow-refresh", None)
        slow_row.append(self.btn_slow_refresh)
        box.append(slow_row)
        self.slow_list = Gtk.ListBox()
        self.slow_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.slow_list)

        self._log_follower = None
        self._log_poll_id = 0
        self._log_follow = True
        self._log_path = ""
        return box

    def _on_log_row_setup(self, _factory, item) -> None:
        lbl = Gtk.Label(xalign=0, wrap=True)
        try:
            lbl.set_wrap_mode(2)  # WORD_CHAR
        except Exception:
            pass
        lbl.add_css_class("monospace")
        item.set_child(lbl)

    def _on_log_row_bind(self, _factory, item) -> None:
        obj = item.get_item()
        text = obj.get_string() if obj is not None else ""
        if len(text) > 2000:
            text = text[:2000] + "…"
        try:
            item.get_child().set_text(text)
        except Exception:
            pass

    def _on_log_pause_toggled(self, btn) -> None:
        paused = bool(btn.get_active())
        self._log_follow = not paused
        self.lbl_log_paused.set_visible(paused)

    def _on_profile_clicked(self, _btn) -> None:
        item = self.drop_profile_dur.get_selected_item()
        text = item.get_string() if item is not None else "10s"
        try:
            duration = int("".join(c for c in text if c.isdigit()) or 10)
        except ValueError:
            duration = 10
        self._emit(_btn, "profile", duration)

    def start_log_poll(self, log_path: str) -> None:
        from odoo_vite.core import log_tail

        self.stop_log_poll()
        self._log_path = log_path or ""
        if not self._log_path:
            self.lbl_log_note.set_text("No log file recorded.")
            return
        self._log_follower = log_tail.LogFollower(self._log_path)
        try:
            initial = log_tail.read_last_n(self._log_path, 500)
        except Exception:
            initial = []
        self._log_follower.sync_to_end()
        self.log_store.splice(0, self.log_store.get_n_items(), [])
        for line in initial:
            self.log_store.append(Gtk.StringObject.new(line[:2000]))
        self.lbl_log_note.set_text(
            f"Tailing {self._log_path} (last {len(initial)} lines shown)")
        self._scroll_log_to_end()
        self._log_poll_id = GLib.timeout_add(1000, self._log_poll_tick)

    def stop_log_poll(self) -> None:
        if self._log_poll_id:
            try:
                GLib.source_remove(self._log_poll_id)
            except Exception:
                pass
            self._log_poll_id = 0
        self._log_follower = None

    def _log_poll_tick(self) -> bool:
        follower = self._log_follower
        if follower is None:
            return False
        try:
            batch = follower.poll()
        except Exception:
            return True
        if batch.get("missing"):
            self.lbl_log_note.set_text(f"Waiting for log file: {self._log_path}")
            return True
        if batch.get("rotated"):
            self.log_store.splice(0, self.log_store.get_n_items(), [])
            self.lbl_log_note.set_text("Log rotated/truncated — restarted from top")
        lines = batch.get("lines", [])
        if lines:
            self._append_log_lines(lines)
        return True

    def _append_log_lines(self, lines: list) -> None:
        adj = None
        try:
            adj = self.log_scrolled.get_vadjustment()
        except Exception:
            pass
        follow = self._log_follow
        if adj is not None and not self.btn_log_pause.get_active():
            try:
                follow = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 8
            except Exception:
                pass
        for line in lines:
            self.log_store.append(Gtk.StringObject.new(line[:2000]))
        over = self.log_store.get_n_items() - self.LOG_MODEL_CAP
        if over > 0:
            self.log_store.splice(0, over, [])
        try:
            paused = (not follow) or self.btn_log_pause.get_active()
            self.lbl_log_paused.set_visible(paused)
        except Exception:
            pass
        if follow and not self.btn_log_pause.get_active():
            self._scroll_log_to_end()

    def _scroll_log_to_end(self) -> None:
        try:
            adj = self.log_scrolled.get_vadjustment()
            if adj is not None:
                adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        except Exception:
            pass

    def set_search_results(self, matches: list, message: str) -> None:
        while True:
            row = self.search_results.get_row_at_index(0)
            if row is None:
                break
            self.search_results.remove(row)
        self.lbl_search_status.set_text(message)
        for match in (matches or [])[:200]:
            row = Gtk.ListBoxRow()
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            head = Gtk.Label(label=f"line {match.get('lineno', '?')}: "
                                   f"{match.get('line', '')}"[:220], xalign=0)
            head.add_css_class("monospace")
            vbox.append(head)
            for ctx in (match.get("before", []) + match.get("after", []))[-4:]:
                lbl = Gtk.Label(label=str(ctx)[:220], xalign=0)
                lbl.add_css_class("dim-label")
                vbox.append(lbl)
            row.set_child(vbox)
            self.search_results.append(row)

    def set_doctor_findings(self, findings: list) -> None:
        while True:
            row = self.doctor_list.get_row_at_index(0)
            if row is None:
                break
            self.doctor_list.remove(row)
        self.doctor_list.set_visible(bool(findings))
        for finding in findings or []:
            row = Gtk.ListBoxRow()
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            count = finding.get("count", 1)
            title = Gtk.Label(
                label=f"[{'!' if finding.get('severity') == 'high' else 'i'}] "
                      f"{finding.get('title', '')}"
                      + (f"  (×{count})" if count > 1 else ""),
                xalign=0, wrap=True)
            if finding.get("severity") == "high":
                title.add_css_class("error")
            vbox.append(title)
            sug = Gtk.Label(label=str(finding.get("suggestion", "")), xalign=0,
                            wrap=True)
            sug.add_css_class("dim-label")
            vbox.append(sug)
            for excerpt in (finding.get("excerpt") or [])[:3]:
                lbl = Gtk.Label(label=str(excerpt)[:220], xalign=0)
                lbl.add_css_class("monospace")
                vbox.append(lbl)
            row.set_child(vbox)
            self.doctor_list.append(row)

    def set_slow_queries(self, ok: bool, message: str, rows: list) -> None:
        while True:
            row = self.slow_list.get_row_at_index(0)
            if row is None:
                break
            self.slow_list.remove(row)
        self.lbl_slow_status.set_text(message)
        for entry in (rows or [])[:20]:
            row = Gtk.ListBoxRow()
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            vbox.append(Gtk.Label(label=str(entry.get("query", ""))[:140],
                                  xalign=0))
            vbox.append(Gtk.Label(
                label=f"{entry.get('calls', 0)} calls · "
                      f"total {entry.get('total_ms', 0)} ms · "
                      f"mean {entry.get('mean_ms', 0)} ms",
                xalign=0))
            row.set_child(vbox)
            self.slow_list.append(row)

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
        self.lbl_desc.set_text(inst.description or "")
        self.lbl_desc.set_visible(bool(inst.description))
        self.refresh_conf()
        self.lbl_conf_notice.set_text("")
        self._stat_rows["db_created"].set_text(
            "yes" if inst.db_created else "not yet (first Start will create it)")
        self._stat_rows["mode"].set_text(
            "Managed (least-privilege)" if (inst.provisioning_mode or "developer") == "managed"
            else "Developer (CREATEDB role)")
        self.show_repair_option(False)
        self.lbl_mod_db.set_text(f"database: {inst.primary_db or '—'}")
        self.set_modules([], {})
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
        self._running = running
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
