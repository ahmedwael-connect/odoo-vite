"""Detail overview tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class OverviewTab:
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

        dev_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.check_devmode = Gtk.CheckButton(label="Dev Mode (auto-restart on file changes)")
        self.check_devmode.set_tooltip_text(
            "Watches custom/community/enterprise addons; restarts the instance "
            "after ~1s of file quiet. Restarts are logged in App events.")
        self.check_devmode.connect("toggled", self._on_devmode_toggled)
        dev_row.append(self.check_devmode)
        self.lbl_devmode = Gtk.Label(xalign=0)
        self.lbl_devmode.add_css_class("dim-label")
        dev_row.append(self.lbl_devmode)
        box.append(dev_row)

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
        ent_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_ent_load = Gtk.Button(label="Load Enterprise…")
        self.btn_ent_load.set_tooltip_text(
            "Clone your licensed Enterprise remote and wire it in")
        self.btn_ent_load.connect("clicked", self._emit, "ent-load", None)
        ent_row.append(self.btn_ent_load)
        self.btn_ent_unload = Gtk.Button(label="Unload Enterprise")
        self.btn_ent_unload.set_tooltip_text(
            "Stop using Enterprise addons (files are kept on disk)")
        self.btn_ent_unload.connect("clicked", self._emit, "ent-unload", None)
        ent_row.append(self.btn_ent_unload)
        box.append(ent_row)

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
