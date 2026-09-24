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
from odoo_vite.ui.detail_tabs.configuration import ConfigurationTab  # noqa: E402
from odoo_vite.ui.detail_tabs.databases import DatabasesTab  # noqa: E402
from odoo_vite.ui.detail_tabs.devtools import DevToolsTab  # noqa: E402
from odoo_vite.ui.detail_tabs.logs import LogsTab  # noqa: E402
from odoo_vite.ui.detail_tabs.modules import ModulesTab  # noqa: E402
from odoo_vite.ui.detail_tabs.overview import OverviewTab  # noqa: E402

OTHER_LABEL = "Other… (type below)"


class InstanceDetailPage(Gtk.Box, OverviewTab, DatabasesTab, ModulesTab,
                         ConfigurationTab, LogsTab, DevToolsTab):
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

    def _scroll_wrap(self, child: Gtk.Widget) -> Gtk.Widget:
        # REG.3: each tab scrolls in its OWN ScrolledWindow. A single shared
        # scroller around the whole Stack lets the tallest tab (e.g. Modules
        # with 600+ rows) drive the viewport size for every other tab,
        # producing long blank scrolls on light tabs like Overview.
        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(child)
        return scrolled

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tab_stack = Gtk.Stack()
        self.tab_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.tab_stack.set_vexpand(True)
        self.tab_stack.set_hexpand(True)
        switcher = Gtk.StackSwitcher(stack=self.tab_stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(6)
        outer.append(switcher)
        self.tab_stack.add_titled(self._scroll_wrap(self._build_overview()),
                                  "overview", "Overview")
        self.tab_stack.add_titled(self._scroll_wrap(self._build_databases()),
                                  "databases", "Databases")
        self.tab_stack.add_titled(self._scroll_wrap(self._build_modules()),
                                  "modules", "Modules")
        self.tab_stack.add_titled(self._scroll_wrap(self._build_devtools()),
                                  "devtools", "Dev Tools")
        self.tab_stack.add_titled(self._scroll_wrap(self._build_logs()),
                                  "logs", "Logs")
        self.tab_stack.add_titled(
            self._scroll_wrap(self._build_configuration()), "configuration",
            "Configuration")
        outer.append(self.tab_stack)
        return outer

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

    def _on_log_follow_toggled(self, btn) -> None:
        # 9.2: ON = following. Toggling off forces pause; toggling back on
        # resumes immediately (scrolls to end on next batch).
        following = bool(btn.get_active())
        self._log_follow = following
        self.lbl_log_paused.set_visible(not following)
        if following:
            self._scroll_log_to_end()

    def _on_log_clear_view(self, _btn) -> None:
        # REG.5: clear the DISPLAYED rows only. The follower keeps its file
        # offset, so new lines keep arriving; the file on disk is untouched.
        try:
            self.log_store.splice(0, self.log_store.get_n_items(), [])
        except Exception:
            pass

    def _on_log_mapped(self, _scrolled) -> None:
        # F2.3: see comment at the connect() site in detail_tabs/logs.py.
        try:
            follow = bool(self._log_follow) and bool(
                self.btn_log_follow.get_active())
        except Exception:
            return
        if follow:
            self._ensure_follow_landing()

    def _ensure_follow_landing(self) -> None:
        # F2.3: ListView layout settles over several frames and any single
        # scroll issued mid-flux (vadjustment set_value AND ListView
        # scroll_to — both verified swallowed) is lost. Converge instead:
        # retry until the viewport is actually at the bottom or tries run
        # out. Single-flight; the normal per-tick follow logic takes over
        # afterwards (including respecting manual scroll-up).
        if getattr(self, "_follow_landing", False):
            return
        self._follow_landing = True
        state = {"tries": 20}

        def _tick() -> bool:
            try:
                follow = bool(self._log_follow) and bool(
                    self.btn_log_follow.get_active())
            except Exception:
                follow = False
            if not follow:
                self._follow_landing = False
                return False
            try:
                adj = self.log_scrolled.get_vadjustment()
                value, upper = adj.get_value(), adj.get_upper()
                page = adj.get_page_size()
            except Exception:
                self._follow_landing = False
                return False
            if value >= upper - page - 8 or state["tries"] <= 0:
                self._follow_landing = False
                return False
            state["tries"] -= 1
            self._scroll_log_to_end()
            return True

        try:
            GLib.timeout_add(150, _tick)
        except Exception:
            self._follow_landing = False

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
        self._ensure_follow_landing()
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
        # 9.2: toggle ON means "follow when at bottom" (auto-pause on
        # scroll-up, auto-resume at bottom); toggle OFF forces pause.
        follow = self._log_follow
        try:
            toggle_on = bool(self.btn_log_follow.get_active())
        except Exception:
            toggle_on = True
        if not toggle_on:
            follow = False
        elif adj is not None:
            try:
                # F2.3: judge against where the bottom was BEFORE this batch
                # arrived, not after. A few new rows grow upper by more than
                # the 8px slack, so a user sitting at the bottom would read
                # as "not at bottom" and stall forever. Allowance = batch
                # size × measured mean row height.
                n0 = self.log_store.get_n_items()
                mean_row = adj.get_upper() / n0 if n0 > 0 else 24
                slack = 8 + len(lines) * mean_row
                follow = (adj.get_value() >=
                          adj.get_upper() - adj.get_page_size() - slack)
            except Exception:
                pass
        for line in lines:
            self.log_store.append(Gtk.StringObject.new(line[:2000]))
        over = self.log_store.get_n_items() - self.LOG_MODEL_CAP
        if over > 0:
            self.log_store.splice(0, over, [])
        try:
            self.lbl_log_paused.set_visible(not follow)
        except Exception:
            pass
        if follow:
            try:
                following = bool(self.btn_log_follow.get_active())
            except Exception:
                following = True
            if following:
                # Converge (single-flight) rather than one blind scroll:
                # layout ripple from the append can leave a single
                # scroll_to ~one row above flush bottom.
                self._ensure_follow_landing()

    def _scroll_log_to_end(self) -> None:
        # F2.3: use the ListView's own scroll_to, not raw vadjustment math.
        # The list lays rows out lazily over several frames; a synchronous
        # set_value lands mid-layout and GTK's own anchoring then moves the
        # viewport again (observed: value 5000 -> 3844 half a second later),
        # permanently stranding follow "not at bottom". scroll_to is queued
        # in the widget and applied by its own layout, so it survives flux.
        # scroll_to alone only guarantees the last row is *visible* (it can
        # stop ~38px above true bottom), so pin flush with set_value after —
        # effective once layout settles, harmless while in flux.
        try:
            n = self.log_store.get_n_items()
            if n > 0:
                self.log_view.scroll_to(
                    n - 1, Gtk.ListScrollFlags.NONE, None)
        except Exception:
            pass
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

    # ------------------------------------------------------- dev tools
    OPERATORS = ["=", "ilike", "!=", ">", "<", ">=", "<="]

    def _on_devmode_toggled(self, btn) -> None:
        try:
            active = bool(btn.get_active())
        except Exception:
            return
        self._emit(btn, "devmode", active)

    def set_devmode_state(self, on: bool, note: str = "") -> None:
        try:
            self.check_devmode.set_active(bool(on))
        except Exception:
            pass
        try:
            self.lbl_devmode.set_text(note)
        except Exception:
            pass

    def _on_shell_send(self, *_args) -> None:
        try:
            text = self.entry_shell_in.get_text() or ""
        except Exception:
            return
        if not text.strip():
            return
        try:
            self.entry_shell_in.set_text("")
        except Exception:
            pass
        self._emit(None, "shell-send", text)

    def shell_append(self, lines: list) -> None:
        try:
            buf = self.shell_output.get_buffer()
            for line in lines or []:
                buf.insert(buf.get_end_iter(), line + "\n")
        except Exception:
            pass

    def shell_set_status(self, text: str) -> None:
        try:
            self.lbl_shell_status.set_text(text)
        except Exception:
            pass

    def test_fields(self):
        try:
            return ((self.entry_test_module.get_text() or "").strip(),
                    (self.entry_test_db.get_text() or "").strip())
        except Exception:
            return "", ""

    # ------------------------------------------------------- dev accessors
    # (Window flows read these; rendering stays here.)
    def rpc_credentials(self):
        try:
            return ((self.entry_rpc_user.get_text() or "").strip(),
                    self.entry_rpc_pass.get_text() or "",
                    bool(self.check_rpc_remember.get_active()))
        except Exception:
            return "", "", False

    def set_rpc_status(self, text: str) -> None:
        try:
            self.lbl_rpc_status.set_text(text)
        except Exception:
            pass

    def set_connection_state(self, connected: bool) -> None:
        for widget in (self.btn_rpc_connect,):
            try:
                widget.set_sensitive(True)
            except Exception:
                pass
        try:
            self.btn_open_code.set_visible(True)
            self.btn_open_cursor.set_visible(True)
        except Exception:
            pass

    def set_editors_visibility(self, editors: dict) -> None:
        try:
            self.btn_open_code.set_visible("code" in (editors or {}))
            self.btn_open_cursor.set_visible("cursor" in (editors or {}))
        except Exception:
            pass

    def selected_model(self):
        try:
            row = None
            selected = self.models_list.get_selected_row()
            if selected is not None:
                return getattr(selected, "model_technical", "")
        except Exception:
            pass
        return ""

    def selected_record(self):
        try:
            selected = self.records_list.get_selected_row()
            if selected is not None:
                return getattr(selected, "record_id", None)
        except Exception:
            pass
        return None

    def render_model_rows(self, models: list) -> None:
        self._models_cache = list(models or [])
        self._render_model_rows()

    def _render_model_rows(self) -> None:
        while True:
            row = self.models_list.get_row_at_index(0)
            if row is None:
                break
            self.models_list.remove(row)
        try:
            needle = (self.entry_model_search.get_text() or "").strip().lower()
        except Exception:
            needle = ""
        for mod in self._models_cache:
            tech, disp = mod.get("technical", ""), mod.get("display", "")
            if needle and needle not in tech.lower() and needle not in disp.lower():
                continue
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            vbox.append(Gtk.Label(label=tech, xalign=0))
            sub = Gtk.Label(label=disp, xalign=0)
            sub.add_css_class("dim-label")
            vbox.append(sub)
            hbox.append(vbox)
            row.set_child(hbox)
            row.model_technical = tech  # type: ignore[attr-defined]
            self.models_list.append(row)

    def _on_model_selected(self, _lb, _row) -> None:
        self._emit(_lb, "model-selected", self.selected_model())

    def set_model_metadata(self, meta: dict) -> None:
        while True:
            row = self.meta_list.get_row_at_index(0)
            if row is None:
                break
            self.meta_list.remove(row)
        meta = meta or {}
        self.lbl_model_meta.set_text(
            f"{meta.get('model', '')}: {len(meta.get('fields', []))} fields, "
            f"{len(meta.get('constraints', []))} constraints, "
            f"{len(meta.get('access', []))} access rules")
        for field in meta.get("fields", [])[:200]:
            row = Gtk.ListBoxRow()
            flags = []
            if field.get("required"):
                flags.append("req")
            if field.get("readonly"):
                flags.append("ro")
            if field.get("compute"):
                flags.append("computed")
            label = (f"{field.get('name', '')} : {field.get('ttype', '')}"
                     + (f" → {field['relation']}" if field.get("relation") else "")
                     + (f"  [{','.join(flags)}]" if flags else ""))
            row.set_child(Gtk.Label(label=label, xalign=0))
            self.meta_list.append(row)
        for kind, items, fmt in (
                ("constraints", meta.get("constraints", []),
                 lambda c: f"[{c.get('type', '')}] {c.get('name', '')}: {c.get('message', '') or c.get('definition', '')}"[:160]),
                ("access", meta.get("access", []),
                 lambda a: f"{a.get('name', '')}: r{a.get('perm_read', 0)}"
                           f"w{a.get('perm_write', 0)}c{a.get('perm_create', 0)}"
                           f"u{a.get('perm_unlink', 0)}")):
            for item in items[:100]:
                row = Gtk.ListBoxRow()
                row.set_child(Gtk.Label(label=f"{kind}: {fmt(item)}", xalign=0))
                self.meta_list.append(row)

    def set_records(self, records: list, offset: int, page_size: int,
                    model: str) -> None:
        while True:
            row = self.records_list.get_row_at_index(0)
            if row is None:
                break
            self.records_list.remove(row)
        self._records_cache = list(records or [])
        self._records_offset = offset
        self._records_model = model
        self.lbl_rec_page.set_text(
            f"{model}: showing {offset + 1}–{offset + len(self._records_cache)}")
        for rec in self._records_cache:
            row = Gtk.ListBoxRow()
            label = f"#{rec.get('id')}  {rec.get('display_name') or rec.get('name', '')}"
            row.set_child(Gtk.Label(label=label[:160], xalign=0))
            row.record_id = rec.get("id")  # type: ignore[attr-defined]
            row.record_label = label  # type: ignore[attr-defined]
            self.records_list.append(row)

    def set_crons(self, crons: list) -> None:
        while True:
            row = self.cron_list.get_row_at_index(0)
            if row is None:
                break
            self.cron_list.remove(row)
        for job in crons or []:
            row = Gtk.ListBoxRow()
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            state = "●" if job.get("active") else "○"
            vbox.append(Gtk.Label(
                label=f"{state} {job.get('name', '')}", xalign=0))
            vbox.append(Gtk.Label(
                label=f"next: {job.get('nextcall', '')} · every {job.get('interval', '')} · "
                      f"{job.get('model', '')}.{job.get('function', '')}()",
                xalign=0))
            row.set_child(vbox)
            self.cron_list.append(row)

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

    def refresh_schedules(self, scheds: list, status: dict) -> None:
        """Render scheduler state + per-schedule rows (BK.3)."""
        from odoo_vite.core import backup_scheduler as _bs

        if status.get("active"):
            state_txt = "Scheduler: active (runs while app is closed)"
        elif status.get("installed"):
            state_txt = ("Scheduler: installed but not running — "
                         "create a schedule to re-enable it")
        else:
            state_txt = ("Scheduler: not installed — creating a schedule "
                         "installs the per-minute systemd timer")
        try:
            self.lbl_sched_status.set_text(state_txt)
        except Exception:
            pass
        while True:
            row = self.sched_list.get_row_at_index(0)
            if row is None:
                break
            self.sched_list.remove(row)
        for sched in scheds or []:
            try:
                nxt = _bs.describe(sched.cron if hasattr(sched, "cron")
                                   else sched.get("cron", ""))
            except Exception:
                nxt = ""
            dbs = ", ".join(sched.databases if hasattr(sched, "databases")
                            else sched.get("databases", []))
            last = (sched.last_run if hasattr(sched, "last_run")
                    else sched.get("last_run", "")) or "never"
            st = (sched.last_status if hasattr(sched, "last_status")
                  else sched.get("last_status", "")) or "—"
            sid = sched.id if hasattr(sched, "id") else sched.get("id", "")
            on = bool(sched.enabled if hasattr(sched, "enabled")
                      else sched.get("enabled", True))
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            cron = sched.cron if hasattr(sched, "cron") else sched.get("cron", "")
            vbox.append(Gtk.Label(
                label=f"{dbs}  ·  {cron}"
                      f"{'' if on else '  (disabled)'}", xalign=0))
            sub = Gtk.Label(label=f"{nxt}  ·  last: {last}  ·  {st}", xalign=0)
            sub.add_css_class("dim-label")
            vbox.append(sub)
            hbox.append(vbox)
            btn_run = Gtk.Button(label="Run Now")
            btn_run.set_tooltip_text("Back up immediately, outside the schedule")
            btn_run.connect("clicked", self._emit, "sched-run-now", sid)
            hbox.append(btn_run)
            btn_toggle = Gtk.Button(label="Disable" if on else "Enable")
            btn_toggle.connect("clicked", self._emit, "sched-toggle", sid)
            hbox.append(btn_toggle)
            btn_del = Gtk.Button(label="Delete")
            btn_del.add_css_class("destructive-action")
            btn_del.connect("clicked", self._emit, "sched-delete", sid)
            hbox.append(btn_del)
            row.set_child(hbox)
            self.sched_list.append(row)

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
        try:
            self.shell_output.get_buffer().set_text("")
            self.shell_set_status("")
            self.entry_test_db.set_text(f"{(inst.primary_db or 'odoo').strip()}_test")
        except Exception:
            pass
        self.lbl_desc.set_text(inst.description or "")
        self.lbl_desc.set_visible(bool(inst.description))
        self.refresh_conf()
        self.lbl_conf_notice.set_text("")
        self.set_rpc_status("")
        self.render_model_rows([])
        self.set_records([], 0, 50, "")
        self.set_crons([])
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
