"""Detail logs tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


class LogsTab:
    def _build_logs(self) -> Gtk.Widget:
        # 9.1: controls (search/doctor) sit ABOVE the greedy tail view, so
        # they are reachable without scrolling. Result/finding lists live in
        # capped scrollers; only the tail expands.
        from gi.repository import Gio as _Gio

        box = self._tab_box()
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Logs", xalign=0, hexpand=True)
        title.add_css_class("heading")
        toolbar.append(title)
        # 9.2: "Follow" toggle (ON = live-follow). Auto-pauses on scroll-up,
        # auto-resumes at the bottom; toggling off forces pause.
        self.btn_log_follow = Gtk.ToggleButton(label="Follow", active=True)
        self.btn_log_follow.set_tooltip_text(
            "Follow new lines as they arrive (pauses automatically when you "
            "scroll up to read)")
        self.btn_log_follow.connect("toggled", self._on_log_follow_toggled)
        toolbar.append(self.btn_log_follow)
        # REG.5: clears the displayed rows only — never touches the log file.
        self.btn_log_clear = Gtk.Button(label="Clear view")
        self.btn_log_clear.set_tooltip_text(
            "Clear the displayed rows only (the log file on disk is "
            "untouched — new lines keep arriving)")
        self.btn_log_clear.connect("clicked", self._on_log_clear_view)
        toolbar.append(self.btn_log_clear)
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
            label="⏸ not following — scroll to the bottom or toggle Follow "
                  "to resume",
            xalign=0)
        self.lbl_log_paused.add_css_class("warning")
        self.lbl_log_paused.set_visible(False)
        box.append(self.lbl_log_paused)
        self.lbl_log_note = Gtk.Label(xalign=0, wrap=True)
        self.lbl_log_note.add_css_class("dim-label")
        box.append(self.lbl_log_note)

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
        search_scrolled = Gtk.ScrolledWindow()
        search_scrolled.set_min_content_height(0)
        search_scrolled.set_max_content_height(170)
        search_scrolled.set_child(self.search_results)
        box.append(search_scrolled)

        self.doctor_list = Gtk.ListBox()
        self.doctor_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.doctor_list.set_visible(False)
        doctor_scrolled = Gtk.ScrolledWindow()
        doctor_scrolled.set_min_content_height(0)
        doctor_scrolled.set_max_content_height(170)
        doctor_scrolled.set_child(self.doctor_list)
        box.append(doctor_scrolled)

        self.log_store = _Gio.ListStore(item_type=Gtk.StringObject)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_log_row_setup)
        factory.connect("bind", self._on_log_row_bind)
        self.log_view = Gtk.ListView(model=Gtk.NoSelection(model=self.log_store),
                                     factory=factory)
        self.log_scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.log_scrolled.set_min_content_height(200)
        self.log_scrolled.set_child(self.log_view)
        box.append(self.log_scrolled)

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
        slow_scrolled = Gtk.ScrolledWindow()
        slow_scrolled.set_min_content_height(0)
        slow_scrolled.set_max_content_height(150)
        slow_scrolled.set_child(self.slow_list)
        box.append(slow_scrolled)

        self._log_follower = None
        self._log_poll_id = 0
        self._log_follow = True
        self._log_path = ""
        return box
