"""Adopt-instance wizard (Sprint 4, Ticket 4.2).

Step 1 — Locate: instance name + conf file picker + community folder
  picker (version detected from release.py, shown immediately).
Step 2 — Review & fill gaps: per-field present/missing report; present
  values editable, missing values required. Enterprise/custom folder
  pickers prefilled from the addons_path heuristic. Primary DB required
  (conf files never record one).
Step 3 — Confirm: summary + Adopt button. Nothing on disk is touched;
  success lands the row in the list with its live status.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
    HAS_NAV_VIEW = hasattr(Adw, "NavigationView")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_NAV_VIEW = False

from odoo_vite.core import adopt, provisioning  # noqa: E402
from odoo_vite.core import registry  # noqa: E402
from odoo_vite.core.adopt import check_enterprise_match  # noqa: E402
from odoo_vite.core.db_manager import is_valid_identifier  # noqa: E402

TOTAL_STEPS = 3


def _shrink(label: Gtk.Label, chars: int = 44) -> Gtk.Label:
    """H-M1: keep long unbroken paths from blowing out window width."""
    label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    label.set_max_width_chars(chars)
    return label


class AdoptInstanceWizard(Gtk.Window):
    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="Adopt Instance — Step 1 of 3")
        self.set_modal(True)
        self.set_default_size(640, 540)
        if parent is not None:
            self.set_transient_for(parent)

        self._conf = ""
        self._community = ""
        self._parsed: dict = {}
        self._report: dict = {}
        self._adopting = False
        self._keyring_ok = True
        self._ent = ""
        self._custom = ""

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        # H-M2: titlebar, not a child (avoids a duplicate native titlebar).
        header = Adw.HeaderBar() if HAS_ADW else Gtk.HeaderBar()
        self.set_titlebar(header)

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
            1: ("locate", "Locate existing install", self._build_locate_page()),
            2: ("gaps", "Review & fill gaps", self._build_gaps_page()),
            3: ("confirm", "Confirm adopt", self._build_confirm_page()),
        }
        if HAS_ADW and HAS_NAV_VIEW:
            self._nav = Adw.NavigationView()
            self._nav_pages = {}
            for num, (_tag, title, widget) in self.pages.items():
                page = Adw.NavigationPage(child=widget, title=title)
                page.set_tag(f"adopt-{num}")
                self._nav_pages[num] = page
            self._nav.push(self._nav_pages[1])
            self._pushed = [1]
            self._use_nav = True
            outer.append(self._nav)
        else:
            self._use_nav = False
            self._stack = Gtk.Stack()
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
            for _num, (tag, _t, widget) in self.pages.items():
                self._stack.add_named(widget, tag)
            outer.append(self._stack)

        self._show_step(1)

    # ------------------------------------------------------------------ nav
    def _show_step(self, step: int) -> None:
        self._step = step
        if self._use_nav:
            if step == 1:
                self._nav.pop_to_page(self._nav_pages[1])
                self._pushed = [1]
            elif step not in self._pushed:
                self._nav.push(self._nav_pages[step])
                self._pushed.append(step)
        else:
            self._stack.set_visible_child_name(self.pages[step][0])
        self.set_title(f"Adopt Instance — Step {step} of {TOTAL_STEPS}")
        self.btn_back.set_visible(step in (2, 3))
        self.btn_close.set_visible(step in (1, 3))
        if step == 2:
            self._fill_gaps()
        if step == 3:
            self._fill_confirm()
        self._update_next()

    def _update_next(self) -> None:
        step = self._step
        self.btn_next.set_visible(step in (1, 2))
        if step == 1:
            self.btn_next.set_sensitive(self._locate_ok())
        elif step == 2:
            self.btn_next.set_sensitive(self._gaps_ok())

    def _on_back(self, _btn: Gtk.Button) -> None:
        if self._step > 1:
            if self._use_nav and len(self._pushed) > 1:
                self._pushed.pop()
                self._nav.pop()
                self._step -= 1
                self.set_title(f"Adopt Instance — Step {self._step} of {TOTAL_STEPS}")
                self.btn_back.set_visible(self._step in (2, 3))
                self.btn_close.set_visible(self._step in (1, 3))
                self._update_next()
            elif not self._use_nav:
                self._show_step(self._step - 1)

    def _on_next(self, _btn: Gtk.Button) -> None:
        if self._step == 1 and self._locate_ok():
            self._show_step(2)
        elif self._step == 2 and self._gaps_ok():
            self._show_step(3)

    def _refresh_parent(self) -> None:
        parent = self.get_transient_for()
        if parent is not None and hasattr(parent, "refresh"):
            try:
                parent.refresh()
            except Exception:
                pass

    # ---------------------------------------------------------- Step 1
    def _build_locate_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Locate the existing Odoo install", xalign=0)
        title.add_css_class("heading")
        box.append(title)

        box.append(Gtk.Label(label="Instance name (must be unique)", xalign=0))
        self.entry_name = Gtk.Entry(placeholder_text="e.g. Legacy 16")
        self.entry_name.connect("changed", lambda _e: self._update_next())
        box.append(self.entry_name)
        self.err_name = Gtk.Label(xalign=0)
        self.err_name.add_css_class("error")
        box.append(self.err_name)

        box.append(Gtk.Label(label="Odoo conf file (required)", xalign=0))
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_conf = _shrink(Gtk.Label(label="No file selected", xalign=0, hexpand=True))
        self.lbl_conf.add_css_class("dim-label")
        row1.append(self.lbl_conf)
        btn_conf = Gtk.Button(label="Browse…")
        btn_conf.connect("clicked", self._on_browse_conf)
        row1.append(btn_conf)
        box.append(row1)

        box.append(Gtk.Label(label="Community folder (contains odoo-bin)", xalign=0))
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_community = _shrink(Gtk.Label(label="No folder selected", xalign=0, hexpand=True))
        self.lbl_community.add_css_class("dim-label")
        row2.append(self.lbl_community)
        btn_comm = Gtk.Button(label="Browse…")
        btn_comm.connect("clicked", self._on_browse_community)
        row2.append(btn_comm)
        box.append(row2)

        self.lbl_detected = Gtk.Label(xalign=0)
        self.lbl_detected.add_css_class("dim-label")
        box.append(self.lbl_detected)
        return box

    def _on_browse_conf(self, _btn: Gtk.Button) -> None:
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title="Select odoo.conf")
            filt = Gtk.FileFilter(name="Config files")
            filt.add_pattern("*.conf")
            filt.add_pattern("*.cfg")
            dlg.set_default_filter(filt)
            dlg.open(self, None, self._on_conf_chosen)
        else:
            dlg = Gtk.FileChooserDialog(
                title="Select odoo.conf", transient_for=self,
                action=Gtk.FileChooserAction.OPEN)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Open", Gtk.ResponseType.ACCEPT)
            dlg.connect("response", self._on_legacy_conf)
            dlg.present()

    def _on_conf_chosen(self, dlg, result) -> None:
        try:
            f = dlg.open_finish(result)
            path = f.get_path() if f else None
        except Exception:
            return
        if path:
            self._conf = path
            self.lbl_conf.set_text(path)
            self._reparse()

    def _on_legacy_conf(self, dlg, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT and dlg.get_file():
            path = dlg.get_file().get_path()
            if path:
                self._conf = path
                self.lbl_conf.set_text(path)
                self._reparse()
        dlg.close()

    def _on_browse_community(self, _btn: Gtk.Button) -> None:
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title="Select community folder (odoo-bin)")
            dlg.select_folder(self, None, self._on_community_chosen)
        else:
            dlg = Gtk.FileChooserDialog(
                title="Select community folder", transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Select", Gtk.ResponseType.ACCEPT)
            dlg.connect("response", self._on_legacy_community)
            dlg.present()

    def _on_community_chosen(self, dlg, result) -> None:
        try:
            f = dlg.select_folder_finish(result)
            path = f.get_path() if f else None
        except Exception:
            return
        if path:
            self._community = path
            self.lbl_community.set_text(path)
            self._reparse()

    def _on_legacy_community(self, dlg, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT and dlg.get_file():
            path = dlg.get_file().get_path()
            if path:
                self._community = path
                self.lbl_community.set_text(path)
                self._reparse()
        dlg.close()

    def _reparse(self) -> None:
        self._parsed = adopt.parse_conf(self._conf) if self._conf else {}
        self._report = adopt.validate_adopted_conf(self._parsed)
        version = adopt.detect_version(self._community) if self._community else ""
        bits = []
        if self._conf:
            missing = [f for f, s in self._report.items() if s == "missing"]
            bits.append("conf parsed" + (f" (missing: {', '.join(missing)})" if missing else " ✓"))
        if self._community:
            ok = (Path(self._community) / "odoo-bin").is_file()
            bits.append(f"odoo-bin {'found' if ok else 'NOT FOUND'}"
                        + (f", version {version}" if version else ""))
        self.lbl_detected.set_text(" · ".join(bits))
        self._update_next()

    def _locate_ok(self) -> bool:
        name = self.entry_name.get_text().strip()
        if not name:
            self.err_name.set_text("Name is required.")
            return False
        if registry.get_instance_by_name(name) is not None:
            self.err_name.set_text(f"An instance named '{name}' already exists.")
            return False
        self.err_name.set_text("")
        if not self._conf or not Path(self._conf).is_file():
            return False
        if not self._community or not (Path(self._community) / "odoo-bin").is_file():
            return False
        return True

    # ---------------------------------------------------------- Step 2
    def _build_gaps_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Review fields & fill gaps", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        note = Gtk.Label(
            label="Present values are editable; missing ones are required. "
                  "Nothing is written back to your conf file.",
            xalign=0, wrap=True)
        note.add_css_class("dim-label")
        box.append(note)

        self.gap_entries: dict[str, Gtk.Entry] = {}
        self.gap_list = Gtk.ListBox()
        self.gap_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.gap_list)

        box.append(Gtk.Label(label="Enterprise addons folder (optional)", xalign=0))
        erow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_ent = _shrink(Gtk.Label(xalign=0, hexpand=True))
        self.lbl_ent.add_css_class("dim-label")
        erow.append(self.lbl_ent)
        btn_ent = Gtk.Button(label="Browse…")
        btn_ent.connect("clicked", lambda _b: self._pick_folder("ent"))
        erow.append(btn_ent)
        box.append(erow)

        box.append(Gtk.Label(label="Custom addons folder (optional)", xalign=0))
        crow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_custom = _shrink(Gtk.Label(xalign=0, hexpand=True))
        self.lbl_custom.add_css_class("dim-label")
        crow.append(self.lbl_custom)
        btn_custom = Gtk.Button(label="Browse…")
        btn_custom.connect("clicked", lambda _b: self._pick_folder("custom"))
        crow.append(btn_custom)
        box.append(crow)

        box.append(Gtk.Label(
            label="Python environment (venv folder, optional — needed to Start)",
            xalign=0))
        vrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry_venv = Gtk.Entry(hexpand=True,
                                    placeholder_text="/path/to/venv (must contain bin/python)")
        self.entry_venv.connect("changed", lambda _e: self._update_next())
        vrow.append(self.entry_venv)
        box.append(vrow)
        self.err_venv = Gtk.Label(xalign=0, wrap=True)
        self.err_venv.add_css_class("error")
        box.append(self.err_venv)

        box.append(Gtk.Label(label="Primary database (required — conf files never record one)", xalign=0))
        self.entry_db = Gtk.Entry()
        self.entry_db.connect("changed", lambda _e: self._update_next())
        box.append(self.entry_db)
        self.err_db = Gtk.Label(xalign=0)
        self.err_db.add_css_class("error")
        box.append(self.err_db)

        box.append(Gtk.Label(label="DB password storage", xalign=0))
        self.lbl_keyring = Gtk.Label(xalign=0, wrap=True)
        self.lbl_keyring.add_css_class("dim-label")
        box.append(self.lbl_keyring)
        self.check_plaintext = Gtk.CheckButton(
            label="I understand the risk, store this password in plaintext locally")
        self.check_plaintext.connect("toggled", lambda _c: self._update_next())
        box.append(self.check_plaintext)
        self.err_secret = Gtk.Label(xalign=0, wrap=True)
        self.err_secret.add_css_class("error")
        box.append(self.err_secret)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(box)
        scrolled.set_child(inner)
        return scrolled

    def _gap_row(self, caption: str, key: str, value: str, missing: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        cap = Gtk.Label(label=f"{caption} — {'MISSING (required)' if missing else 'present'}",
                        xalign=0)
        vbox.append(cap)
        entry = Gtk.Entry(text=value or "")
        entry.connect("changed", lambda _e: self._update_next())
        vbox.append(entry)
        hbox.append(vbox)
        row.set_child(hbox)
        self.gap_entries[key] = entry
        return row

    def _fill_gaps(self) -> None:
        while True:
            row = self.gap_list.get_row_at_index(0)
            if row is None:
                break
            self.gap_list.remove(row)
        self.gap_entries = {}
        p = self._parsed
        port = p.get("xmlrpc_port") or p.get("http_port") or ""
        fields = [("Addons path", "addons_path", p.get("addons_path", ""),
                   self._report.get("addons_path") == "missing"),
                  ("DB user", "db_user", p.get("db_user", ""),
                   self._report.get("db_user") == "missing"),
                  ("DB password", "db_password", p.get("db_password", ""),
                   self._report.get("db_password") == "missing"),
                  ("Port", "port", port,
                   self._report.get("port") == "missing"),
                  ("Log file", "logfile", p.get("logfile", ""),
                   self._report.get("logfile") == "missing")]
        for caption, key, value, missing in fields:
            self.gap_list.append(self._gap_row(caption, key, value, missing))
        parts = adopt.split_addons(p.get("addons_path", ""))
        self._ent = parts["enterprise"]
        self._custom = parts["custom"]
        self.lbl_ent.set_text(self._ent or "Not set")
        self.lbl_custom.set_text(self._custom or "Not set")
        if not self.entry_db.get_text().strip():
            name = self.entry_name.get_text().strip()
            self.entry_db.set_text(provisioning.slugify_db_name(name or "odoo"))
        self.lbl_keyring.set_text("checking OS keyring…")
        self._update_enterprise_note()
        self._update_next()

        def _probe() -> None:
            try:
                ok = registry.keyring_available()
            except Exception:
                ok = False
            GLib.idle_add(self._on_keyring_probed, ok)

        threading.Thread(target=_probe, daemon=True).start()

    def _on_keyring_probed(self, ok: bool) -> bool:
        self._keyring_ok = ok
        self.lbl_keyring.set_text(
            "OS keyring available — password will be stored securely."
            if ok else
            "No working OS keyring found. Passwords can only be stored with "
            "the explicit plaintext opt-out below.")
        self._update_next()
        return False

    def _pick_folder(self, which: str) -> None:
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title="Select folder")
            dlg.select_folder(self, None,
                              lambda d, r: self._on_gap_folder(d, r, which))
        else:
            dlg = Gtk.FileChooserDialog(
                title="Select folder", transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Select", Gtk.ResponseType.ACCEPT)

            def _resp(d, response, _which=which):
                if response == Gtk.ResponseType.ACCEPT and d.get_file():
                    self._set_gap_folder(_which, d.get_file().get_path())
                d.close()

            dlg.connect("response", _resp)
            dlg.present()

    def _on_gap_folder(self, dlg, result, which: str) -> None:
        try:
            f = dlg.select_folder_finish(result)
            path = f.get_path() if f else None
        except Exception:
            return
        if path:
            self._set_gap_folder(which, path)

    def _set_gap_folder(self, which: str, path: str) -> None:
        if which == "ent":
            self._ent = path
            self.lbl_ent.set_text(path)
            self._update_enterprise_note()
        else:
            self._custom = path
            self.lbl_custom.set_text(path)

    def _update_enterprise_note(self) -> None:
        """H.5 set-time note: enterprise major vs detected Odoo version."""
        if not getattr(self, "_ent", ""):
            return
        try:
            version = adopt.detect_version(self._community)
            info = check_enterprise_match(version, self._ent)
        except Exception:
            info = {"match": None, "enterprise_major": ""}
        if info.get("match") is True:
            self.lbl_ent.set_text(f"{self._ent}  (Enterprise {info.get('enterprise_major')} ✓)")
        elif info.get("match") is False:
            self.lbl_ent.set_text(
                f"{self._ent}  (⚠ Enterprise {info.get('enterprise_major')} ≠ "
                f"Odoo {version} — allowed, verify yourself)")

    def _gaps_ok(self) -> bool:
        get = lambda k: self.gap_entries.get(k).get_text().strip() if k in self.gap_entries else ""
        ok = True
        if not get("addons_path"):
            ok = False
        if not get("db_user") or not is_valid_identifier(get("db_user")):
            ok = False
        try:
            port = int(get("port") or "0")
            if not (1 <= port <= 65535):
                ok = False
        except ValueError:
            ok = False
        if not get("logfile"):
            ok = False
        db = self.entry_db.get_text().strip()
        if not is_valid_identifier(db):
            self.err_db.set_text("Lowercase letters, digits and _ only.")
            ok = False
        else:
            self.err_db.set_text("")
        venv = self.entry_venv.get_text().strip()
        if venv and not os.path.isfile(os.path.join(venv, "bin", "python")):
            self.err_venv.set_text(f"Not a virtualenv: no bin/python under {venv}")
            ok = False
        else:
            self.err_venv.set_text("")
        pw = self.gap_entries.get("db_password").get_text() \
            if "db_password" in self.gap_entries else ""
        if pw and not self._keyring_ok and not self.check_plaintext.get_active():
            self.err_secret.set_text(
                "No OS keyring available — enable a Secret Service or "
                "explicitly tick the plaintext opt-out.")
            ok = False
        else:
            self.err_secret.set_text("")
        return ok

    # ---------------------------------------------------------- Step 3
    def _build_confirm_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        title = Gtk.Label(label="Confirm adopt", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        box.append(Gtk.Label(
            label="Only a registry row will be created. Your files, conf and "
                  "database are never modified.",
            xalign=0, wrap=True))
        self.confirm_list = Gtk.ListBox()
        self.confirm_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.confirm_list)

        self.btn_adopt = Gtk.Button(label="Adopt Instance")
        self.btn_adopt.add_css_class("suggested-action")
        self.btn_adopt.set_margin_top(12)
        self.btn_adopt.connect("clicked", self._on_adopt_clicked)
        box.append(self.btn_adopt)

        self.lbl_adopt_error = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.lbl_adopt_error.add_css_class("error")
        box.append(self.lbl_adopt_error)
        self.spinner = Gtk.Spinner()
        box.append(self.spinner)
        return box

    def _crow(self, caption: str, value: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)
        hbox.append(Gtk.Label(label=caption, xalign=0, hexpand=True))
        val = _shrink(Gtk.Label(label=value, xalign=1))
        val.add_css_class("dim-label")
        hbox.append(val)
        row.set_child(hbox)
        return row

    def _collect_overrides(self) -> dict:
        get = lambda k: self.gap_entries.get(k).get_text().strip() if k in self.gap_entries else ""
        return {
            "addons_path_note": get("addons_path"),
            "db_user": get("db_user"),
            "db_password": self.gap_entries.get("db_password").get_text()
            if "db_password" in self.gap_entries else "",
            "port": get("port"),
            "logfile": get("logfile"),
            "venv_path": self.entry_venv.get_text().strip(),
            "enterprise_path": getattr(self, "_ent", "") or None,
            "custom_addons_path": getattr(self, "_custom", "") or "",
            "primary_db": self.entry_db.get_text().strip(),
            "version": adopt.detect_version(self._community),
        }

    def _fill_confirm(self) -> None:
        while True:
            row = self.confirm_list.get_row_at_index(0)
            if row is None:
                break
            self.confirm_list.remove(row)
        ov = self._collect_overrides()
        for caption, value in [
                ("Name", self.entry_name.get_text().strip()),
                ("Conf", self._conf),
                ("Community", self._community),
                ("Version", ov["version"] or "unknown"),
                ("Port", ov["port"]),
                ("DB user", ov["db_user"]),
                ("Primary DB", ov["primary_db"]),
                ("Venv", ov.get("venv_path") or "— (set later on the detail page)"),
                ("Enterprise", ov["enterprise_path"] or "—"),
                ("Custom addons", ov["custom_addons_path"] or "—")]:
            self.confirm_list.append(self._crow(caption, value))

    def _on_adopt_clicked(self, _btn: Gtk.Button) -> None:
        if self._adopting:
            return
        self._adopting = True
        self.btn_adopt.set_sensitive(False)
        self.lbl_adopt_error.set_text("")
        self.spinner.start()
        name = self.entry_name.get_text().strip()
        # addons_path itself isn't an Instance column; enterprise/custom travel
        # as their own overrides (conf file stays untouched on disk).
        ov = self._collect_overrides()
        overrides = {k: v for k, v in ov.items() if k != "addons_path_note"}

        def _work() -> None:
            res = adopt.adopt_instance(name, self._conf, self._community,
                                       overrides=overrides,
                                       allow_plaintext=self.check_plaintext.get_active())
            GLib.idle_add(self._on_adopt_done, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _on_adopt_done(self, ok: bool, message: str) -> bool:
        self._adopting = False
        self.spinner.stop()
        if ok:
            self._refresh_parent()
            self.close()
        else:
            self.lbl_adopt_error.set_text(message)
            self.btn_adopt.set_sensitive(True)
        return False
