"""Detail databases tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class DatabasesTab:
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
