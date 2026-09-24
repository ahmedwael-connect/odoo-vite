"""Detail devtools tab builder (Sprint R.8 — moved verbatim; `self` is the page)."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


class DevToolsTab:
    def _build_devtools(self) -> Gtk.Widget:
        box = self._tab_box()
        conn_title = Gtk.Label(label="Odoo RPC connection", xalign=0)
        conn_title.add_css_class("heading")
        box.append(conn_title)
        box.append(Gtk.Label(
            label="Introspection talks to the RUNNING instance over XML-RPC "
                  "(no cold-start cost per call). Start the instance first.",
            xalign=0, wrap=True))
        conn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_rpc_user = Gtk.Entry(hexpand=True, placeholder_text="Odoo user (e.g. admin)")
        conn_row.append(self.entry_rpc_user)
        self.entry_rpc_pass = Gtk.Entry(hexpand=True, placeholder_text="Password",
                                        visibility=False)
        conn_row.append(self.entry_rpc_pass)
        self.check_rpc_remember = Gtk.CheckButton(label="Remember (keyring)")
        conn_row.append(self.check_rpc_remember)
        self.btn_rpc_connect = Gtk.Button(label="Connect")
        self.btn_rpc_connect.connect("clicked", self._emit, "rpc-connect", None)
        conn_row.append(self.btn_rpc_connect)
        box.append(conn_row)
        self.lbl_rpc_status = Gtk.Label(xalign=0, wrap=True)
        self.lbl_rpc_status.add_css_class("dim-label")
        box.append(self.lbl_rpc_status)

        insp_title = Gtk.Label(label="Model inspector", xalign=0)
        insp_title.add_css_class("heading")
        box.append(insp_title)
        insp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_model_search = Gtk.SearchEntry(
            hexpand=True, placeholder_text="Search models…")
        self.entry_model_search.connect("search-changed",
                                        lambda _e: self._render_model_rows())
        insp_row.append(self.entry_model_search)
        box.append(insp_row)
        self.models_list = Gtk.ListBox()
        self.models_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.models_list.connect("row-selected", self._on_model_selected)
        models_scrolled = Gtk.ScrolledWindow()
        models_scrolled.set_min_content_height(120)
        models_scrolled.set_max_content_height(220)
        models_scrolled.set_child(self.models_list)
        box.append(models_scrolled)
        self.lbl_model_meta = Gtk.Label(xalign=0, wrap=True)
        self.lbl_model_meta.add_css_class("dim-label")
        box.append(self.lbl_model_meta)
        self.meta_list = Gtk.ListBox()
        self.meta_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.meta_list)

        rec_title = Gtk.Label(label="Records", xalign=0)
        rec_title.add_css_class("heading")
        box.append(rec_title)
        dom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_dom_field = Gtk.Entry(hexpand=True, placeholder_text="field (e.g. name)")
        dom_row.append(self.entry_dom_field)
        self.drop_dom_op = Gtk.DropDown(model=Gtk.StringList.new(self.OPERATORS))
        dom_row.append(self.drop_dom_op)
        self.entry_dom_value = Gtk.Entry(hexpand=True, placeholder_text="value")
        dom_row.append(self.entry_dom_value)
        btn_dom_go = Gtk.Button(label="Search")
        btn_dom_go.connect("clicked", self._emit, "rec-search", None)
        dom_row.append(btn_dom_go)
        box.append(dom_row)
        nav_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_rec_prev = Gtk.Button(label="‹ Prev")
        self.btn_rec_prev.connect("clicked", self._emit, "rec-prev", None)
        nav_row.append(self.btn_rec_prev)
        self.lbl_rec_page = Gtk.Label(hexpand=True)
        self.lbl_rec_page.add_css_class("dim-label")
        nav_row.append(self.lbl_rec_page)
        self.btn_rec_next = Gtk.Button(label="Next ›")
        self.btn_rec_next.connect("clicked", self._emit, "rec-next", None)
        nav_row.append(self.btn_rec_next)
        box.append(nav_row)
        self.records_list = Gtk.ListBox()
        self.records_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        box.append(self.records_list)
        crud_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_rec_new = Gtk.Button(label="New…")
        self.btn_rec_new.connect("clicked", self._emit, "rec-new", None)
        crud_row.append(self.btn_rec_new)
        self.btn_rec_edit = Gtk.Button(label="Edit…")
        self.btn_rec_edit.connect("clicked", self._emit, "rec-edit", None)
        crud_row.append(self.btn_rec_edit)
        self.btn_rec_delete = Gtk.Button(label="Delete…")
        self.btn_rec_delete.add_css_class("destructive-action")
        self.btn_rec_delete.connect("clicked", self._emit, "rec-delete", None)
        crud_row.append(self.btn_rec_delete)
        box.append(crud_row)

        cron_title = Gtk.Label(label="Cron jobs", xalign=0)
        cron_title.add_css_class("heading")
        box.append(cron_title)
        cron_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_cron_refresh = Gtk.Button(label="Refresh crons")
        self.btn_cron_refresh.connect("clicked", self._emit, "cron-refresh", None)
        cron_row.append(self.btn_cron_refresh)
        box.append(cron_row)
        self.cron_list = Gtk.ListBox()
        self.cron_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.cron_list)

        exp_title = Gtk.Label(label="Export & editors", xalign=0)
        exp_title.add_css_class("heading")
        box.append(exp_title)
        exp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_launch_json = Gtk.Button(label="Generate launch.json")
        self.btn_launch_json.connect("clicked", self._emit, "gen-launch", None)
        exp_row.append(self.btn_launch_json)
        self.btn_open_code = Gtk.Button(label="Open in VS Code")
        self.btn_open_code.connect("clicked", self._emit, "open-code", None)
        exp_row.append(self.btn_open_code)
        self.btn_open_cursor = Gtk.Button(label="Open in Cursor")
        self.btn_open_cursor.connect("clicked", self._emit, "open-cursor", None)
        exp_row.append(self.btn_open_cursor)
        box.append(exp_row)

        self._models_cache = []
        self._records_cache = []
        self._records_offset = 0
        self._records_model = ""

        shell_title = Gtk.Label(label="Odoo Shell (interactive)", xalign=0)
        shell_title.add_css_class("heading")
        box.append(shell_title)
        box.append(Gtk.Label(
            label="Direct subprocess REPL — no network hop. Validates "
                  "venv/odoo-bin/conf/database before spawning.",
            xalign=0, wrap=True))
        shell_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_shell_start = Gtk.Button(label="Start shell")
        self.btn_shell_start.connect("clicked", self._emit, "shell-start", None)
        shell_btn_row.append(self.btn_shell_start)
        self.btn_shell_stop = Gtk.Button(label="Stop shell")
        self.btn_shell_stop.connect("clicked", self._emit, "shell-stop", None)
        shell_btn_row.append(self.btn_shell_stop)
        self.lbl_shell_status = Gtk.Label(xalign=0, hexpand=True)
        self.lbl_shell_status.add_css_class("dim-label")
        shell_btn_row.append(self.lbl_shell_status)
        box.append(shell_btn_row)
        self.shell_output = Gtk.TextView(editable=False, monospace=True,
                                         hexpand=True)
        shell_scrolled = Gtk.ScrolledWindow()
        shell_scrolled.set_min_content_height(200)
        shell_scrolled.set_max_content_height(320)
        shell_scrolled.set_child(self.shell_output)
        box.append(shell_scrolled)
        shell_in_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_shell_in = Gtk.Entry(hexpand=True,
                                        placeholder_text=">>> type python, Enter to send")
        self.entry_shell_in.connect("activate", self._on_shell_send)
        shell_in_row.append(self.entry_shell_in)
        btn_shell_send = Gtk.Button(label="Send")
        btn_shell_send.connect("clicked", self._on_shell_send)
        shell_in_row.append(btn_shell_send)
        box.append(shell_in_row)

        test_title = Gtk.Label(label="Run module tests", xalign=0)
        test_title.add_css_class("heading")
        box.append(test_title)
        test_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_test_module = Gtk.Entry(hexpand=True,
                                           placeholder_text="module technical name")
        test_row.append(self.entry_test_module)
        self.entry_test_db = Gtk.Entry(hexpand=True,
                                       placeholder_text="test database")
        test_row.append(self.entry_test_db)
        self.btn_test_run = Gtk.Button(label="Run Tests")
        self.btn_test_run.add_css_class("suggested-action")
        self.btn_test_run.connect("clicked", self._emit, "test-run", None)
        test_row.append(self.btn_test_run)
        box.append(test_row)
        box.append(Gtk.Label(
            label="Defaults to <primary>_test. NEVER the primary database — "
                  "refused outright. Missing DBs are created+installed (-i); "
                  "existing ones are updated+tested (-u).",
            xalign=0, wrap=True))
        return box
