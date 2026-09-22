"""'New Instance' wizard, Steps 1–6 (Sprint 1 Ticket 1.5 + Sprint 2 Ticket 2.2).

Step 1 — Version picker (live GitHub branches, debounced search).
Step 2 — System check (checklist + pkexec fix).
Step 3 — Instance details form (name/port/db user/db password/db name,
  live validation: unique name, free port, identifier-safe db name).
Step 4 — Enterprise addons (optional folder picker + heuristic validation).
Step 5 — Review & confirm (read-only summary + Create Instance button).
Step 6 — Provisioning progress (live log, Cancel/Retry/Discard/Open).

Core calls run in daemon threads, UI updates via GLib.idle_add — the GTK
main loop is never blocked. Uses Adw.NavigationView when available,
Gtk.Stack otherwise.
"""

from __future__ import annotations

import secrets
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
    HAS_NAV_VIEW = hasattr(Adw, "NavigationView")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_NAV_VIEW = False

from odoo_vite.core import git_manager, provisioning, system_check  # noqa: E402
from odoo_vite.core import registry  # noqa: E402
from odoo_vite.core.adopt import check_enterprise_match  # noqa: E402
from odoo_vite.core.db_manager import is_valid_identifier  # noqa: E402
from odoo_vite.core.instance import Instance  # noqa: E402
from odoo_vite.core.settings import get_provisioning_mode  # noqa: E402

SEARCH_DEBOUNCE_MS = 300
TOTAL_STEPS = 6


def _password_entry() -> Gtk.Widget:
    if hasattr(Gtk, "PasswordEntry"):
        return Gtk.PasswordEntry(show_peek_icon=True)  # type: ignore[attr-defined]
    entry = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
    return entry


class CreateInstanceWizard(Gtk.Window):
    """Modal wizard window. `selected_version` is set once the user picks one."""

    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="New Instance — Step 1 of 6")
        self.selected_version: str | None = None
        self.set_modal(True)
        self.set_default_size(680, 560)
        if parent is not None:
            self.set_transient_for(parent)

        self._debounce_id: int | None = None
        self._name_debounce_id: int | None = None
        self._fetch_seq = 0  # guards against out-of-order thread results
        self._form = {
            "name": "", "port": 8069, "db_user": "odoo",
            "db_password": "odoo", "db_name": "", "enterprise_path": None,
        }
        self._dbname_dirty = False
        self._form_valid = False
        self._keyring_ok = True  # refined by a background probe on step 3
        self._provision_instance: Instance | None = None
        self._cancel_event: threading.Event | None = None
        self._provisioning = False
        self._provision_ok = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)

        header = Adw.HeaderBar() if HAS_ADW else Gtk.HeaderBar()
        outer.append(header)

        self.btn_back = Gtk.Button(label="‹ Back")
        self.btn_back.connect("clicked", self._on_back)
        self.btn_next = Gtk.Button(label="Next ›")
        self.btn_next.add_css_class("suggested-action")
        self.btn_next.connect("clicked", self._on_next)
        self.btn_close = Gtk.Button(label="Close")
        self.btn_close.connect("clicked", lambda _b: self.close())
        if hasattr(header, "pack_start"):
            header.pack_start(self.btn_back)
            header.pack_start(self.btn_close)
        if hasattr(header, "pack_end"):
            header.pack_end(self.btn_next)

        self.pages = {
            1: ("version", "Choose Odoo version", self._build_version_page()),
            2: ("syscheck", "System check", self._build_syscheck_page()),
            3: ("details", "Instance details", self._build_details_page()),
            4: ("enterprise", "Enterprise addons", self._build_enterprise_page()),
            5: ("review", "Review & confirm", self._build_review_page()),
            6: ("progress", "Creating instance", self._build_progress_page()),
        }

        if HAS_ADW and HAS_NAV_VIEW:
            self._nav = Adw.NavigationView()
            self._nav_pages = {}
            for num, (tag, title, widget) in self.pages.items():
                page = Adw.NavigationPage(child=widget, title=title)
                page.set_tag(tag)
                self._nav_pages[num] = page
            self._nav.push(self._nav_pages[1])
            self._pushed = [1]
            self._use_nav = True
            outer.append(self._nav)
        else:
            self._use_nav = False
            self._stack = Gtk.Stack()
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
            for _num, (tag, _title, widget) in self.pages.items():
                self._stack.add_named(widget, tag)
            outer.append(self._stack)

        self.connect("close-request", self._on_close_request)
        self._show_step(1)
        self._load_branches("")  # initial fetch in background

    # ------------------------------------------------------------------ nav
    def _show_step(self, step: int) -> None:
        self._step = step
        tag = self.pages[step][0]
        if self._use_nav:
            if step == 1:
                self._nav.pop_to_page(self._nav_pages[1])
                self._pushed = [1]
            elif step == self._pushed[-1] - 1 and len(self._pushed) > 1:
                self._pushed.pop()
                self._nav.pop()
            elif step not in self._pushed:
                self._nav.push(self._nav_pages[step])
                self._pushed.append(step)
        else:
            self._stack.set_visible_child_name(tag)
        self.set_title(f"New Instance — Step {step} of {TOTAL_STEPS}")
        self.btn_back.set_visible(step in (2, 3, 4, 5))
        self.btn_close.set_visible(step in (1, 6))
        if step == 3:
            self._prefill_details()
        if step == 5:
            self._fill_review()
        self._update_next_sensitivity()

    def _update_next_sensitivity(self) -> None:
        step = self._step
        self.btn_next.set_visible(step in (1, 2, 3, 4))
        if step == 1:
            self.btn_next.set_sensitive(self.selected_version is not None)
        elif step == 3:
            self.btn_next.set_sensitive(self._form_valid)
        else:
            self.btn_next.set_sensitive(True)

    def _on_back(self, _btn: Gtk.Button) -> None:
        if self._step > 1:
            self._show_step(self._step - 1)

    def _on_next(self, _btn: Gtk.Button) -> None:
        if self._step == 1 and self.selected_version:
            self._show_step(2)
            self._run_system_check()
        elif self._step == 2:
            self._show_step(3)
        elif self._step == 3 and self._form_valid:
            self._show_step(4)
        elif self._step == 4:
            self._show_step(5)

    def _on_close_request(self, *_args) -> bool:
        if self._cancel_event is not None:
            self._cancel_event.set()  # stop provisioning if the window closes
        return False  # allow the close

    # ------------------------------------------------------- Step 1: versions
    def _build_version_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        label = Gtk.Label(label="Select the Odoo version to install", xalign=0)
        label.add_css_class("heading")
        box.append(label)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Search versions, e.g. 17.0")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search_changed)
        search_row.append(self.search)
        self.spinner_v = Gtk.Spinner()
        search_row.append(self.spinner_v)
        box.append(search_row)

        self.branch_status = Gtk.Label(xalign=0)
        self.branch_status.add_css_class("dim-label")
        box.append(self.branch_status)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(scrolled)
        self.branch_list = Gtk.ListBox()
        self.branch_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.branch_list.connect("row-selected", self._on_branch_selected)
        scrolled.set_child(self.branch_list)
        return box

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, self._on_search_debounced
        )

    def _on_search_debounced(self) -> bool:
        self._debounce_id = None
        self._load_branches(self.search.get_text())
        return False  # one-shot

    def _load_branches(self, search: str) -> None:
        self._fetch_seq += 1
        seq = self._fetch_seq
        self.spinner_v.start()
        self.branch_status.set_text("Loading versions from github.com…")

        def _work() -> None:
            res = git_manager.list_odoo_branches(search=search)
            GLib.idle_add(self._on_branches_loaded, seq, res.ok, res.message,
                           list(res.data or []))

        threading.Thread(target=_work, daemon=True).start()

    def _on_branches_loaded(self, seq: int, ok: bool, message: str,
                            branches: list) -> bool:
        if seq != self._fetch_seq:
            return False  # a newer search superseded this result
        self.spinner_v.stop()
        while True:
            row = self.branch_list.get_row_at_index(0)
            if row is None:
                break
            self.branch_list.remove(row)
        if not ok:
            self.branch_status.set_text(f"Error: {message}")
            return False
        self.branch_status.set_text(message)
        for name in branches:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(6)
            hbox.set_margin_bottom(6)
            lbl = Gtk.Label(label=f"Odoo {name}", xalign=0, hexpand=True)
            check = Gtk.Label(label="")
            hbox.append(lbl)
            hbox.append(check)
            row.set_child(hbox)
            # NOTE: PyGObject removed GObject.set_data(); plain attributes used.
            row.version_name = name  # type: ignore[attr-defined]
            row.check_label = check  # type: ignore[attr-defined]
            if name == self.selected_version:
                self.branch_list.select_row(row)
                check.set_text("✓")
            self.branch_list.append(row)
        if not branches:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label="No matching versions"))
            row.set_sensitive(False)
            self.branch_list.append(row)
        return False

    def _on_branch_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        for i in range(10000):
            r = self.branch_list.get_row_at_index(i)
            if r is None:
                break
            chk = getattr(r, "check_label", None)
            if chk is not None:
                chk.set_text("")
        if row is None or not row.is_sensitive():
            self.selected_version = None
        else:
            self.selected_version = getattr(row, "version_name", None)
            chk = getattr(row, "check_label", None)
            if chk is not None:
                chk.set_text("✓")
        self._update_next_sensitivity()

    # ----------------------------------------------------- Step 2: sys check
    def _build_syscheck_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        self.sys_title = Gtk.Label(xalign=0)
        self.sys_title.add_css_class("heading")
        box.append(self.sys_title)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner_s = Gtk.Spinner()
        status_row.append(self.spinner_s)
        self.sys_status = Gtk.Label(xalign=0, hexpand=True)
        self.sys_status.add_css_class("dim-label")
        status_row.append(self.sys_status)
        box.append(status_row)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(scrolled)
        self.check_list = Gtk.ListBox()
        self.check_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self.check_list)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_fix = Gtk.Button(label="Fix automatically (pkexec)")
        self.btn_fix.add_css_class("suggested-action")
        self.btn_fix.connect("clicked", self._on_fix_clicked)
        action_row.append(self.btn_fix)
        self.btn_recheck = Gtk.Button(label="Re-check")
        self.btn_recheck.connect("clicked", lambda _b: self._run_system_check())
        action_row.append(self.btn_recheck)
        box.append(action_row)

        self.log_scrolled = Gtk.ScrolledWindow()
        self.log_scrolled.set_min_content_height(140)
        self.log_scrolled.set_visible(False)
        self.log_view = Gtk.TextView(editable=False, monospace=True)
        self.log_scrolled.set_child(self.log_view)
        box.append(self.log_scrolled)
        return box

    def _run_system_check(self) -> None:
        version = self.selected_version or ""
        self.sys_title.set_text(f"Requirements for Odoo {version}")
        self.spinner_s.start()
        self.sys_status.set_text("Checking system…")
        self.btn_fix.set_sensitive(False)
        self.btn_recheck.set_sensitive(False)

        def _work() -> None:
            res = system_check.check_requirements(version)
            GLib.idle_add(self._on_syscheck_done, res.ok, res.message,
                           res.data if isinstance(res.data, dict) else {})

        threading.Thread(target=_work, daemon=True).start()

    def _on_syscheck_done(self, ok: bool, message: str, report: dict) -> bool:
        self.spinner_s.stop()
        self.btn_recheck.set_sensitive(True)
        while True:
            row = self.check_list.get_row_at_index(0)
            if row is None:
                break
            self.check_list.remove(row)
        if not ok or not report:
            self.sys_status.set_text(f"Check failed: {message}")
            return False
        missing = report.get("missing", [])
        details = report.get("details", {})
        self.sys_status.set_text(message)
        for req in system_check.REQUIREMENTS:
            info = details.get(req.name, {})
            present = bool(info.get("present", False))
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            icon = Gtk.Label(label="✅" if present else "❌")
            hbox.append(icon)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(label=req.label, xalign=0)
            hint = Gtk.Label(label="" if present else req.hint, xalign=0)
            hint.add_css_class("dim-label")
            vbox.append(title)
            if not present:
                vbox.append(hint)
            hbox.append(vbox)
            row.set_child(hbox)
            self.check_list.append(row)
        extra = []
        if not report.get("python_version_ok", True):
            extra.append(f"Python {report.get('python_version')} < required {report.get('min_python')}")
        if report.get("node_ok") is False:
            extra.append(f"Node v{report.get('node_version')} < required v{report.get('min_node', '?')}")
        if extra:
            self.sys_status.set_text(message + " | " + "; ".join(extra))
        self._missing = missing
        self.btn_fix.set_visible(bool(missing))
        self.btn_fix.set_sensitive(bool(missing))
        return False

    def _log_line(self, view: Gtk.TextView, scrolled: Gtk.ScrolledWindow, line: str) -> None:
        buf = view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        adj = scrolled.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper())

    def _on_fix_clicked(self, _btn: Gtk.Button) -> None:
        missing = getattr(self, "_missing", [])
        if not missing:
            return
        self.btn_fix.set_sensitive(False)
        self.btn_recheck.set_sensitive(False)
        self.log_scrolled.set_visible(True)
        self._log_line(self.log_view, self.log_scrolled, "$ pkexec apt-get install -y …")
        self.spinner_s.start()

        def _feed(line: str) -> None:
            GLib.idle_add(lambda: self._append_fix_line(line))

        def _append_fix_line(line: str) -> bool:
            self._log_line(self.log_view, self.log_scrolled, line)
            return False

        def _work() -> None:
            res = system_check.install_requirements(missing, on_line=_feed)
            GLib.idle_add(self._on_fix_done, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _on_fix_done(self, ok: bool, message: str) -> bool:
        self.spinner_s.stop()
        self._log_line(self.log_view, self.log_scrolled,
                       ("Done: " if ok else "Failed: ") + message)
        self.btn_recheck.set_sensitive(True)
        # Refresh the checklist so the user sees what changed.
        self._run_system_check()
        return False

    # ------------------------------------------------- Step 3: details form
    def _field_row(self, box: Gtk.Box, caption: str) -> tuple[Gtk.Label, Gtk.Label]:
        lbl = Gtk.Label(label=caption, xalign=0)
        box.append(lbl)
        err = Gtk.Label(xalign=0)
        err.add_css_class("error")
        return lbl, err

    def _build_details_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Instance details", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        _, self.err_name = self._field_row(box, "Instance name (must be unique)")
        self.entry_name = Gtk.Entry(placeholder_text="e.g. Client A — Odoo 17")
        self.entry_name.connect("changed", self._on_name_changed)
        box.append(self.entry_name)
        box.append(self.err_name)

        _, self.err_port = self._field_row(box, "Port")
        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spin_port = Gtk.SpinButton.new_with_range(1024, 65535, 1)
        self.spin_port.set_value(8069)
        self.spin_port.connect("value-changed", lambda _s: self._validate_form())
        port_row.append(self.spin_port)
        self.lbl_port_hint = Gtk.Label(xalign=0)
        self.lbl_port_hint.add_css_class("dim-label")
        port_row.append(self.lbl_port_hint)
        box.append(port_row)
        box.append(self.err_port)

        _, self.err_dbuser = self._field_row(box, "Database user")
        self.entry_dbuser = Gtk.Entry(text="odoo")
        self.entry_dbuser.connect("changed", lambda _e: self._validate_form())
        box.append(self.entry_dbuser)
        box.append(self.err_dbuser)

        box.append(Gtk.Label(label="Database password", xalign=0))
        pw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_dbpass = _password_entry()
        self.entry_dbpass.set_hexpand(True)
        if hasattr(self.entry_dbpass, "set_text"):
            self.entry_dbpass.set_text("odoo")
        pw_row.append(self.entry_dbpass)
        btn_gen = Gtk.Button(label="Generate")
        btn_gen.set_tooltip_text("Generate a strong random password")
        btn_gen.connect("clicked", self._on_generate_password)
        pw_row.append(btn_gen)
        box.append(pw_row)
        self.err_dbpass = Gtk.Label(xalign=0)
        self.err_dbpass.add_css_class("error")
        box.append(self.err_dbpass)
        self.lbl_keyring = Gtk.Label(xalign=0, wrap=True)
        self.lbl_keyring.add_css_class("dim-label")
        box.append(self.lbl_keyring)
        self.check_plaintext = Gtk.CheckButton(
            label="I understand the risk, store this password in plaintext locally")
        self.check_plaintext.connect("toggled", lambda _c: self._validate_form())
        box.append(self.check_plaintext)

        _, self.err_dbname = self._field_row(box, "Database name")
        self.entry_dbname = Gtk.Entry()
        self.entry_dbname.connect("changed", self._on_dbname_changed)
        box.append(self.entry_dbname)
        box.append(self.err_dbname)
        return box

    def _on_generate_password(self, _btn: Gtk.Button) -> None:
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
        pw = "".join(secrets.choice(alphabet) for _ in range(20))
        self.entry_dbpass.set_text(pw)
        self._validate_form()

    def _entry_text(self, entry: Gtk.Widget) -> str:
        if hasattr(entry, "get_text"):
            try:
                return entry.get_text() or ""
            except Exception:
                return ""
        return ""

    def _on_name_changed(self, _entry: Gtk.Entry) -> None:
        name = self._entry_text(self.entry_name).strip()
        self._form["name"] = name
        if not self._dbname_dirty:
            self.entry_dbname.set_text(provisioning.slugify_db_name(name))
        # Debounced uniqueness check (~300ms) per spec; rest validates now.
        if self._name_debounce_id is not None:
            GLib.source_remove(self._name_debounce_id)
        self._name_debounce_id = GLib.timeout_add(300, self._on_name_debounced)
        self._validate_form(check_name_unique=False)

    def _on_name_debounced(self) -> bool:
        self._name_debounce_id = None
        self._validate_form(check_name_unique=True)
        return False

    def _on_dbname_changed(self, _entry: Gtk.Entry) -> None:
        self._dbname_dirty = True
        self._form["db_name"] = self._entry_text(self.entry_dbname).strip()
        self._validate_form()

    def _prefill_details(self) -> None:
        """Suggest port + probe keyring off-loop, then fill via idle."""
        self.lbl_port_hint.set_text("finding a free port…")
        self.lbl_keyring.set_text("checking OS keyring…")

        def _work() -> None:
            port = provisioning.suggest_port(8069)
            try:
                keyring_ok = registry.keyring_available()
            except Exception:
                keyring_ok = False
            GLib.idle_add(self._on_details_prefill, port, keyring_ok)

        threading.Thread(target=_work, daemon=True).start()

    def _on_details_prefill(self, port: int, keyring_ok: bool) -> bool:
        if int(self.spin_port.get_value_as_int()) == 8069:
            self.spin_port.set_value(port)
        self.lbl_port_hint.set_text("")
        self._keyring_ok = keyring_ok
        if keyring_ok:
            self.lbl_keyring.set_text("OS keyring available — password will be stored securely.")
        else:
            self.lbl_keyring.set_text(
                "No working OS keyring found (Secret Service unavailable). "
                "Enable one (e.g. 'sudo apt install gnome-keyring', then "
                "log out/in) or tick the opt-out below.")
        self._validate_form()
        return False

    def _validate_form(self, check_name_unique: bool = True) -> bool:
        name = self._entry_text(self.entry_name).strip()
        port = int(self.spin_port.get_value_as_int())
        db_user = self._entry_text(self.entry_dbuser).strip()
        db_pass = self._entry_text(self.entry_dbpass)
        db_name = self._entry_text(self.entry_dbname).strip()
        self._form.update({"name": name, "port": port, "db_user": db_user or "odoo",
                           "db_password": db_pass, "db_name": db_name})

        ok = True
        # name
        if not name:
            self.err_name.set_text("Name is required.")
            ok = False
        elif check_name_unique and registry.get_instance_by_name(name) is not None:
            self.err_name.set_text(f"An instance named '{name}' already exists.")
            ok = False
        else:
            self.err_name.set_text("")
        # port
        if not (1024 <= port <= 65535):
            self.err_port.set_text("Port must be between 1024 and 65535.")
            ok = False
        elif not provisioning.is_port_free(port):
            self.err_port.set_text(f"Port {port} is already in use.")
            ok = False
        else:
            self.err_port.set_text("")
        # db user
        if not is_valid_identifier(db_user or "odoo"):
            self.err_dbuser.set_text("Lowercase letters, digits and _ only; must not start with a digit.")
            ok = False
        else:
            self.err_dbuser.set_text("")
        # db password (+ H.2 keyring gate)
        if not db_pass:
            self.err_dbpass.set_text("Password is required.")
            ok = False
        elif not self._keyring_ok and not self.check_plaintext.get_active():
            self.err_dbpass.set_text(
                "No OS keyring available — enable a Secret Service or "
                "explicitly tick the plaintext opt-out below.")
            ok = False
        else:
            self.err_dbpass.set_text("")
        # db name
        if not is_valid_identifier(db_name):
            self.err_dbname.set_text(
                "Lowercase letters, digits and _ only; must not start with a digit.")
            ok = False
        else:
            self.err_dbname.set_text("")

        self._form_valid = ok
        if getattr(self, "_step", 1) == 3:
            self._update_next_sensitivity()
        return ok

    # ---------------------------------------------- Step 4: enterprise addons
    def _build_enterprise_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Odoo Enterprise addons (optional)", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toggle_row.append(Gtk.Label(label="I have Enterprise addons", hexpand=True, xalign=0))
        self.switch_enterprise = Gtk.Switch(active=False)
        self.switch_enterprise.connect("state-set", self._on_enterprise_toggled)
        toggle_row.append(self.switch_enterprise)
        box.append(toggle_row)

        self.ent_picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pick_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_ent_path = Gtk.Label(label="No folder selected", xalign=0, hexpand=True)
        self.lbl_ent_path.add_css_class("dim-label")
        pick_row.append(self.lbl_ent_path)
        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", self._on_browse_enterprise)
        pick_row.append(btn_browse)
        self.ent_picker_box.append(pick_row)
        self.lbl_ent_warn = Gtk.Label(xalign=0, wrap=True)
        self.lbl_ent_warn.add_css_class("warning")
        self.ent_picker_box.append(self.lbl_ent_warn)
        self.lbl_ent_version = Gtk.Label(xalign=0, wrap=True)
        self.lbl_ent_version.add_css_class("dim-label")
        self.ent_picker_box.append(self.lbl_ent_version)
        self.ent_picker_box.set_visible(False)
        box.append(self.ent_picker_box)

        note = Gtk.Label(
            label="Enterprise is validated heuristically (a subfolder containing "
                  "__manifest__.py). A warning here never blocks — you may know "
                  "better than our check.",
            xalign=0, wrap=True)
        note.add_css_class("dim-label")
        box.append(note)
        return box

    def _on_enterprise_toggled(self, _sw: Gtk.Switch, state: bool) -> bool:
        self.ent_picker_box.set_visible(state)
        if not state:
            self._form["enterprise_path"] = None
            self.lbl_ent_path.set_text("No folder selected")
            self.lbl_ent_warn.set_text("")
            self.lbl_ent_version.set_text("")
        return False  # allow the toggle

    def _on_browse_enterprise(self, _btn: Gtk.Button) -> None:
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title="Select Enterprise addons folder")
            dlg.select_folder(self, None, self._on_folder_chosen)
        else:  # older GTK fallback
            dlg = Gtk.FileChooserDialog(
                title="Select Enterprise addons folder", transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Select", Gtk.ResponseType.ACCEPT)
            dlg.connect("response", self._on_legacy_folder_response)
            dlg.present()

    def _on_folder_chosen(self, dlg, result) -> None:
        try:
            folder = dlg.select_folder_finish(result)
            path = folder.get_path() if folder is not None else None
        except Exception:
            return  # dismissed — keep previous selection
        if path:
            self._set_enterprise_path(path)

    def _on_legacy_folder_response(self, dlg, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            path = dlg.get_file().get_path() if dlg.get_file() else None
            if path:
                self._set_enterprise_path(path)
        dlg.close()

    def _set_enterprise_path(self, path: str) -> None:
        from pathlib import Path

        self._form["enterprise_path"] = path
        self.lbl_ent_path.set_text(path)
        root = Path(path)
        valid = False
        try:
            valid = any((sub / "__manifest__.py").is_file()
                        for sub in root.iterdir() if sub.is_dir())
        except OSError:
            valid = False
        if valid:
            self.lbl_ent_warn.set_text("Looks like an Enterprise addons folder. ✓")
        else:
            self.lbl_ent_warn.set_text(
                "Warning: no subfolder with __manifest__.py found here. "
                "You may still proceed.")
        self._update_enterprise_version_note(path)

    def _update_enterprise_version_note(self, path: str) -> None:
        """H.5 set-time warning: enterprise major vs chosen Odoo version."""
        try:
            info = check_enterprise_match(self.selected_version or "", path)
        except Exception:
            info = {"match": None, "enterprise_major": ""}
        if info.get("match") is True:
            self.lbl_ent_version.set_text(
                f"Enterprise {info.get('enterprise_major')} matches "
                f"Odoo {self.selected_version}. ✓")
        elif info.get("match") is False:
            self.lbl_ent_version.set_text(
                f"⚠ Enterprise {info.get('enterprise_major')} does NOT match "
                f"Odoo {self.selected_version} — proceeding anyway is allowed, "
                "but expect breakage.")
        else:
            self.lbl_ent_version.set_text(
                "Could not determine the enterprise version — no blocking, "
                "but verify compatibility yourself.")

    # ------------------------------------------------- Step 5: review
    def _build_review_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Review & confirm", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        self.review_list = Gtk.ListBox()
        self.review_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.review_list)

        self.btn_create = Gtk.Button(label="Create Instance")
        self.btn_create.add_css_class("suggested-action")
        self.btn_create.set_margin_top(12)
        self.btn_create.connect("clicked", self._on_create_clicked)
        box.append(self.btn_create)
        return box

    def _review_row(self, caption: str, value: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)
        hbox.append(Gtk.Label(label=caption, xalign=0, hexpand=True))
        val = Gtk.Label(label=value, xalign=1)
        val.add_css_class("dim-label")
        hbox.append(val)
        row.set_child(hbox)
        return row

    def _fill_review(self) -> None:
        while True:
            row = self.review_list.get_row_at_index(0)
            if row is None:
                break
            self.review_list.remove(row)
        f = self._form
        try:
            mode = get_provisioning_mode()
        except Exception:
            mode = "developer"
        mode_text = ("Developer — DB role gets CREATEDB"
                     if mode == "developer" else
                     "Managed — least-privilege role, DB ops prompt separately")
        if self._keyring_ok:
            storage_text = "OS keyring (secure)"
        elif self.check_plaintext.get_active():
            storage_text = "Plaintext SQLite (explicit opt-out — audited)"
        else:
            storage_text = "BLOCKED — no keyring and no opt-out"
        rows = [
            ("Odoo version", self.selected_version or "—"),
            ("Instance name", f["name"]),
            ("Port", str(f["port"])),
            ("DB user", f["db_user"]),
            ("DB password", "•" * min(len(f["db_password"]), 12) + f" ({storage_text})"),
            ("DB name", f["db_name"]),
            ("Enterprise", f["enterprise_path"] or "— (community only)"),
            ("Provisioning mode", mode_text),
            ("Folder", str(provisioning.unique_instance_path(f["name"] or "odoo"))),
        ]
        for caption, value in rows:
            self.review_list.append(self._review_row(caption, value))

    def _on_create_clicked(self, _btn: Gtk.Button) -> None:
        self._show_step(6)
        self._start_provisioning()

    # -------------------------------------------- Step 6: provisioning
    def _build_progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner_p = Gtk.Spinner()
        status_row.append(self.spinner_p)
        self.provision_status = Gtk.Label(xalign=0, hexpand=True)
        self.provision_status.add_css_class("heading")
        status_row.append(self.provision_status)
        box.append(status_row)

        self.provision_error = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.provision_error.add_css_class("error")
        box.append(self.provision_error)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(220)
        self.provision_log = Gtk.TextView(editable=False, monospace=True)
        scrolled.set_child(self.provision_log)
        self.provision_scrolled = scrolled
        box.append(scrolled)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_cancel = Gtk.Button(label="Cancel")
        self.btn_cancel.connect("clicked", self._on_cancel_provision)
        btn_row.append(self.btn_cancel)
        self.btn_retry = Gtk.Button(label="Retry")
        self.btn_retry.add_css_class("suggested-action")
        self.btn_retry.connect("clicked", lambda _b: self._start_provisioning())
        btn_row.append(self.btn_retry)
        self.btn_discard = Gtk.Button(label="Discard draft")
        self.btn_discard.add_css_class("destructive-action")
        self.btn_discard.connect("clicked", self._on_discard_clicked)
        btn_row.append(self.btn_discard)
        self.btn_open = Gtk.Button(label="Open Instance")
        self.btn_open.add_css_class("suggested-action")
        self.btn_open.connect("clicked", self._on_open_instance)
        btn_row.append(self.btn_open)
        box.append(btn_row)
        return box

    def _build_instance(self) -> Instance:
        f = self._form
        if self._provision_instance is None:
            path = provisioning.unique_instance_path(f["name"] or "odoo")
            self._provision_instance = Instance(
                name=f["name"],
                version=self.selected_version or "",
                mode="managed",
                path=str(path),
                venv_path=str(path / "venv"),
                community_path=str(path / "community"),
                enterprise_path=f["enterprise_path"],
                custom_addons_path=str(path / "custom_addons"),
                conf_path=str(path / "odoo.conf"),
                log_path=str(path / "logs" / "odoo.log"),
                port=int(f["port"]),
                db_user=f["db_user"] or "odoo",
                db_password=f["db_password"],
                password_storage="plaintext",  # resolved to keyring at register
                primary_db=f["db_name"],
                tracked_dbs=[f["db_name"]] if f["db_name"] else [],
                status="draft",
            )
        else:  # Retry: refresh mutable form values, keep id + path stable
            inst = self._provision_instance
            inst.version = self.selected_version or inst.version
            inst.port = int(f["port"])
            inst.db_user = f["db_user"] or "odoo"
            inst.db_password = f["db_password"]
            inst.enterprise_path = f["enterprise_path"]
            inst.primary_db = f["db_name"]
            inst.tracked_dbs = [f["db_name"]] if f["db_name"] else []
        return self._provision_instance

    def _start_provisioning(self) -> None:
        if self._provisioning:
            return
        self._provisioning = True
        self._provision_ok = False
        self.btn_retry.set_visible(False)
        self.btn_discard.set_visible(False)
        self.btn_open.set_visible(False)
        self.btn_cancel.set_visible(True)
        self.btn_cancel.set_sensitive(True)
        self.provision_error.set_text("")
        self.provision_log.get_buffer().set_text("")
        self.provision_status.set_text("Creating instance…")
        self.spinner_p.start()

        inst = self._build_instance()
        self._cancel_event = threading.Event()

        def _feed(line: str) -> None:
            GLib.idle_add(lambda: self._append_provision_line(line))

        def _work() -> None:
            res = provisioning.provision_instance(
                inst, progress_cb=_feed, cancel=self._cancel_event.is_set,
                allow_plaintext=self.check_plaintext.get_active())
            GLib.idle_add(self._on_provision_done, res.ok, res.message,
                           dict(res.data or {}))

        threading.Thread(target=_work, daemon=True).start()

    def _append_provision_line(self, line: str) -> bool:
        self._log_line(self.provision_log, self.provision_scrolled, line)
        return False

    def _on_provision_done(self, ok: bool, message: str, data: dict) -> bool:
        self._provisioning = False
        self.spinner_p.stop()
        self.btn_cancel.set_visible(False)
        if ok:
            self._provision_ok = True
            self.provision_status.set_text("Instance ready ✓")
            self.btn_open.set_visible(True)
            self._refresh_parent()
        else:
            failed = data.get("failed_step", "?")
            self.provision_status.set_text(f"Failed at step: {failed}")
            self.provision_error.set_text(message)
            self.btn_retry.set_visible(True)
            self.btn_discard.set_visible(True)
        return False

    def _on_cancel_provision(self, _btn: Gtk.Button) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.btn_cancel.set_sensitive(False)
        self.provision_status.set_text("Cancelling… (waiting on subprocess)")

    def _on_discard_clicked(self, _btn: Gtk.Button) -> None:
        if self._provision_instance is None:
            self.close()
            return
        self.btn_retry.set_sensitive(False)
        self.btn_discard.set_sensitive(False)
        self.provision_status.set_text("Discarding draft…")
        inst_id = self._provision_instance.id

        def _work() -> None:
            res = provisioning.discard_draft(inst_id)
            GLib.idle_add(self._on_discard_done, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _on_discard_done(self, ok: bool, message: str) -> bool:
        if ok:
            self._provision_instance = None
            self._refresh_parent()
            self.close()
        else:
            self.provision_error.set_text(f"Discard failed: {message}")
            self.btn_retry.set_sensitive(True)
            self.btn_discard.set_sensitive(True)
        return False

    def _on_open_instance(self, _btn: Gtk.Button) -> None:
        # Detail page is a Sprint 3 stub — land on the refreshed list for now.
        self._refresh_parent()
        self.close()

    def _refresh_parent(self) -> None:
        parent = self.get_transient_for()
        if parent is not None and hasattr(parent, "refresh"):
            try:
                parent.refresh()
            except Exception:
                pass
