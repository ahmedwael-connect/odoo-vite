"""Detail configuration tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402


class ConfigurationTab:
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
