"""Detail modules tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class ModulesTab:
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
