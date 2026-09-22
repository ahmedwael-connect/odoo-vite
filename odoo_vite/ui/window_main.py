"""Main window shell (Sprint 1 shell + Sprint 3 lifecycle wiring).

Owns: sidebar/detail layout, 2s get_statuses() polling (background thread,
in-place row updates), Start/Stop/Restart flows, first-start confirmation
and DB-collision dialogs, pill CSS. Uses libadwaita when available.
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
    HAS_ALERT = hasattr(Adw, "AlertDialog")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_ALERT = False
    BaseWindow = Gtk.ApplicationWindow
else:
    BaseWindow = Adw.ApplicationWindow

from odoo_vite.core import process_manager  # noqa: E402
from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui.page_instance_detail import InstanceDetailPage  # noqa: E402
from odoo_vite.ui.page_instance_list import InstanceListPage  # noqa: E402
from odoo_vite.ui.settings_dialog import show_settings_dialog  # noqa: E402
from odoo_vite.ui.wizard_adopt_instance import AdoptInstanceWizard  # noqa: E402
from odoo_vite.ui.wizard_create_instance import CreateInstanceWizard  # noqa: E402

POLL_MS = 2000

PILL_CSS = """
.status-running { color: #2ec27e; font-weight: bold; }
.status-stopped { color: #9a9996; }
.status-error { color: #e01b24; font-weight: bold; }
.status-draft { color: #e5a50a; }
.error { color: #e01b24; }
.warning { color: #e5a50a; }
"""


# ------------------------------------------------ blocking dialog helpers
# NOTE: these block the calling thread waiting on a GTK dialog, so they must
# be called from a background thread (all lifecycle flows are). They marshal
# the dialog itself onto the main loop via GLib.idle_add.

def _ask_blocking(parent, heading, body, responses, default_id="cancel",
                  entry_default=None):
    # responses: [(id, label, appearance-or-None)]
    done = threading.Event()
    out: dict = {}
    entry_holder: dict = {}

    def _chosen(response_id):
        out["id"] = response_id
        if entry_default is not None:
            widget = entry_holder.get("entry")
            try:
                out["text"] = widget.get_text() if widget else ""
            except Exception:
                out["text"] = ""
        done.set()

    def _show():
        try:
            if HAS_ADW and HAS_ALERT:
                dlg = Adw.AlertDialog(heading=heading, body=body)
                for rid, label, appearance in responses:
                    dlg.add_response(rid, label)
                    if appearance is not None:
                        dlg.set_response_appearance(rid, appearance)
                if entry_default is not None:
                    entry = Gtk.Entry(text=entry_default)
                    entry_holder["entry"] = entry
                    dlg.set_extra_child(entry)
                dlg.set_default_response(default_id)
                dlg.set_close_response(default_id)
                dlg.choose(parent, None,
                           lambda d, t: _chosen(_finish_alert(d, t, default_id)))
            elif HAS_ADW and hasattr(Adw, "MessageDialog"):
                dlg = Adw.MessageDialog(transient_for=parent,
                                        heading=heading, body=body)
                for rid, label, appearance in responses:
                    dlg.add_response(rid, label)
                    if appearance is not None:
                        dlg.set_response_appearance(rid, appearance)
                dlg.set_default_response(default_id)
                dlg.set_close_response(default_id)
                dlg.connect("response", lambda d, r: _chosen(r))
                dlg.present()
            else:
                ids = [rid for rid, _l, _a in responses]
                dlg = Gtk.MessageDialog(
                    transient_for=parent, modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.NONE, text=heading)
                dlg.format_secondary_text(body)
                for i, (_rid, label, _a) in enumerate(responses):
                    dlg.add_button(label, i)
                entry = None
                if entry_default is not None:
                    entry = Gtk.Entry(text=entry_default)
                    entry_holder["entry"] = entry
                    dlg.get_message_area().append(entry)
                dlg.connect("response",
                            lambda d, i: _chosen(ids[i] if 0 <= i < len(ids) else default_id))
                dlg.present()
        except Exception:
            _chosen(default_id)
        return False

    GLib.idle_add(_show)
    done.wait(timeout=300)
    return out.get("id", default_id), out.get("text", "")


def _finish_alert(dlg, task, default_id):
    try:
        return dlg.choose_finish(task)
    except Exception:
        return default_id


def filter_checks(checks: dict, needle: str) -> int:
    """H-P1 client-side filter: show checks matching needle, return visible count."""
    needle = (needle or "").strip().lower()
    visible = 0
    for name, check in checks.items():
        show = not needle or needle in name.lower()
        try:
            check.set_visible(show)
        except Exception:
            pass
        if show:
            visible += 1
    return visible


class MainWindow(BaseWindow):  # type: ignore[misc]
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Odoo Vite")
        self.set_default_size(1000, 660)

        css = Gtk.CssProvider()
        css.load_from_string(PILL_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if HAS_ADW and hasattr(Adw, "ToastOverlay"):
            self._toasts = Adw.ToastOverlay()
            self._toasts.set_child(outer)
            self.set_content(self._toasts)
        else:
            self._toasts = None
            self.set_content(outer)

        header = Adw.HeaderBar() if HAS_ADW else Gtk.HeaderBar()
        outer.append(header)

        # H-M3: global busy indicator for background lifecycle operations.
        self.header_spinner = Gtk.Spinner()
        if hasattr(header, "pack_start"):
            header.pack_start(self.header_spinner)
        self._bg_ops = 0

        self.btn_new = Gtk.Button(label="+ New Instance")
        self.btn_new.add_css_class("suggested-action")
        self.btn_new.connect("clicked", self._on_new_instance)
        if hasattr(header, "pack_end"):
            header.pack_end(self.btn_new)
        self.btn_adopt = Gtk.Button(label="Adopt")
        self.btn_adopt.set_tooltip_text("Bring an existing Odoo install under management")
        self.btn_adopt.connect("clicked", self._on_adopt_instance)
        if hasattr(header, "pack_end"):
            header.pack_end(self.btn_adopt)
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.set_tooltip_text("Menu")
        popover = Gtk.Popover()
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pop_box.set_margin_start(8)
        pop_box.set_margin_end(8)
        pop_box.set_margin_top(8)
        pop_box.set_margin_bottom(8)
        btn_prefs = Gtk.Button(label="Preferences")
        btn_prefs.connect("clicked", self._on_preferences)
        pop_box.append(btn_prefs)
        popover.set_child(pop_box)
        self.menu_btn.set_popover(popover)
        if hasattr(header, "pack_end"):
            header.pack_end(self.menu_btn)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(300)
        paned.set_shrink_start_child(False)
        paned.set_resize_start_child(False)
        outer.append(paned)

        self.sidebar = InstanceListPage(on_action=self._on_row_action,
                                        on_select=self._on_select)
        paned.set_start_child(self.sidebar)

        self.detail = InstanceDetailPage(on_action=self._on_row_action)
        paned.set_end_child(self.detail)

        self._poll_busy = False
        self._poll_id = GLib.timeout_add(POLL_MS, self._poll_tick)
        self.connect("close-request", self._on_close_request)
        GLib.idle_add(self._poll_tick_soon)

    # ------------------------------------------------------------------ shell
    def _on_close_request(self, *_args) -> bool:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        return False

    def _on_new_instance(self, _btn: Gtk.Button | None = None) -> None:
        wizard = CreateInstanceWizard(parent=self)
        wizard.present()

    def _on_adopt_instance(self, _btn: Gtk.Button | None = None) -> None:
        wizard = AdoptInstanceWizard(parent=self)
        wizard.present()

    def _on_preferences(self, _btn: Gtk.Button | None = None) -> None:
        self.menu_btn.get_popover().popdown()
        show_settings_dialog(self, on_changed=lambda: self.toast(
            "Settings saved — applies to newly created instances"))

    def toast(self, message: str) -> None:
        """Transient success notification (PM pattern: toast for resolved events)."""
        if self._toasts is not None and HAS_ADW:
            try:
                self._toasts.add_toast(Adw.Toast.new(message))
            except Exception:
                pass

    def refresh(self) -> None:
        self.sidebar.full_refresh()

    def _on_select(self, instance_id: str) -> None:
        self.detail.show_instance(instance_id)

    # ---------------------------------------------------------------- polling
    def _poll_tick_soon(self) -> bool:
        self._poll_tick()
        return False

    def _poll_tick(self) -> bool:
        if self._poll_busy:
            return True
        self._poll_busy = True

        def _work() -> None:
            try:
                statuses = process_manager.get_statuses()
            except Exception:
                statuses = []
            GLib.idle_add(self._apply_statuses, statuses)

        threading.Thread(target=_work, daemon=True).start()
        return True

    def _apply_statuses(self, statuses: list) -> bool:
        try:
            self.sidebar.set_statuses(statuses)
            if self.detail.instance_id:
                for status in statuses:
                    if status.get("id") == self.detail.instance_id:
                        self.detail.update_status(status)
                        break
        finally:
            self._poll_busy = False
        return False

    # ----------------------------------------------------------------- actions
    def _on_row_action(self, action: str, instance_id: str, payload=None) -> None:
        if action == "start":
            self.start_flow(instance_id)
        elif action in ("stop", "restart"):
            self._run_simple_flow(action, instance_id)
        elif action == "repair":
            self._repair_flow(instance_id)
        elif action == "set-primary":
            self._set_primary_flow(instance_id, (payload or "").strip())
        elif action == "switch":
            self._switch_flow(instance_id, (payload or "").strip())
        elif action == "track":
            self._track_flow(instance_id, (payload or "").strip())
        elif action == "untrack":
            self._untrack_flow(instance_id, (payload or "").strip())
        elif action == "discover":
            self._discover_flow(instance_id)
        elif action == "remove":
            self._remove_flow(instance_id)
        elif action == "secured":
            self.toast(str(payload or "Password secured"))

    # ------------------------------------------------ H-M3 busy sweep
    # Every bg lifecycle op brackets itself with _op_start/_op_end (always on
    # the main thread: starts run in click handlers, ends in idle callbacks).
    # While busy: header spinner spins and all action buttons go insensitive,
    # so every click gets immediate feedback and double-clicks can't pile up.
    def _op_start(self) -> None:
        self._bg_ops += 1
        self.header_spinner.start()
        self._set_actions_sensitive(False)

    def _op_end(self) -> None:
        self._bg_ops = max(0, self._bg_ops - 1)
        if self._bg_ops == 0:
            self.header_spinner.stop()
            self._set_actions_sensitive(True)

    def _set_actions_sensitive(self, sensitive: bool) -> None:
        try:
            for row in self.sidebar._rows.values():
                row.btn_start.set_sensitive(sensitive)
                row.btn_stop.set_sensitive(sensitive)
                row.btn_restart.set_sensitive(sensitive)
        except Exception:
            pass
        for btn in (self.detail.btn_start, self.detail.btn_stop,
                    self.detail.btn_restart, self.detail.btn_switch,
                    self.detail.btn_set_primary, self.detail.btn_discover,
                    self.detail.btn_remove, self.detail.btn_browser):
            try:
                btn.set_sensitive(sensitive)
            except Exception:
                pass

    def _run_simple_flow(self, action: str, instance_id: str) -> None:
        self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            if action == "stop":
                res = process_manager.stop_instance(instance_id)
            else:
                res = process_manager.restart_instance(instance_id)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def start_flow(self, instance_id: str, database: str | None = None,
                   _op_held: bool = False) -> None:
        """Start flow with first-start confirm + collision handling (bg thread)."""
        if not _op_held:
            self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            res = process_manager.start_instance(
                instance_id, database=database, confirm_cb=self._confirm_cb)
            if not res.ok and (res.data or {}).get("collision"):
                # Op stays held across the recursive retry (balanced by its end).
                self._handle_collision(instance_id, res.data)
                return
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _repair_flow(self, instance_id: str) -> None:
        self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            from odoo_vite.core import venv_manager

            res = venv_manager.repair_venv(instance_id)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_result(self, instance_id: str, ok: bool, message: str) -> bool:
        self._op_end()
        self._select_row(instance_id)
        if ok:
            self.toast(message)
            if self.detail.instance_id == instance_id:
                self.detail.show_error("")
                self.detail.show_repair_option(False)
        else:
            # PM pattern: persistent label (+ status pill) for ongoing bad states.
            if self.detail.instance_id == instance_id:
                self.detail.show_error(message)
                # H-B1: surface one-click repair on the pkg_resources pattern.
                self.detail.show_repair_option("pkg_resources" in message)
        self._poll_tick()
        return False

    def _select_row(self, instance_id: str) -> None:
        def _do() -> bool:
            row = self.sidebar._rows.get(instance_id)
            if row is not None:
                self.sidebar.listbox.select_row(row)
            else:
                self.detail.show_instance(instance_id)
            return False

        GLib.idle_add(_do)

    # ------------------------------------------------- confirm + collision
    def _confirm_cb(self, preview: dict) -> bool:
        """Runs in a background thread; shows the exact first-start command."""
        if preview.get("reinit"):
            heading = "Complete database initialization?"
            body = (
                f"Database '{preview['db_name']}' exists but was never "
                f"initialized (interrupted setup). This will complete it by "
                f"running:\n{preview['command_str']}\n\nProceed?")
            confirm_label = "Initialize & Start"
        else:
            heading = "Create database and start?"
            body = (
                f"Instance '{preview['instance_name']}' has never been started.\n\n"
                f"This will create database '{preview['db_name']}' by running:\n"
                f"{preview['command_str']}\n\n"
                "Proceed?")
            confirm_label = "Create & Start"
        answer, _ = _ask_blocking(
            self, heading, body,
            [("cancel", "Cancel", None),
             ("confirm", confirm_label, Adw.ResponseAppearance.SUGGESTED if HAS_ADW else None)],
            default_id="cancel")
        return answer == "confirm"

    def _handle_collision(self, instance_id: str, data: dict) -> None:
        db_name = data.get("db_name", "?")
        choice, _ = _ask_blocking(
            self, "Database already exists",
            f"Database '{db_name}' already exists. Reuse it as-is, "
            "pick a different name (it will be created on start), or abort?",
            [("abort", "Abort", None),
             ("new", "Pick new name", None),
             ("reuse", "Reuse existing",
              Adw.ResponseAppearance.SUGGESTED if HAS_ADW else None)],
            default_id="abort")
        if choice == "reuse":
            update_instance(instance_id, db_created=True)
            self.start_flow(instance_id, _op_held=True)
        elif choice == "new":
            _choice2, text = _ask_blocking(
                self, "New database name",
                f"Enter a new database name for this instance "
                f"(current: '{db_name}'):",
                [("cancel", "Cancel", None),
                 ("ok", "Use this name",
                  Adw.ResponseAppearance.SUGGESTED if HAS_ADW else None)],
                default_id="cancel", entry_default=f"{db_name}_2")
            new_name = (text or "").strip()
            from odoo_vite.core.db_manager import is_valid_identifier

            if _choice2 == "ok" and new_name and is_valid_identifier(new_name):
                update_instance(instance_id, primary_db=new_name)
                self.start_flow(instance_id, _op_held=True)
            else:
                GLib.idle_add(self._show_result, instance_id, False,
                              "Aborted — no valid new database name given")
        else:
            GLib.idle_add(self._show_result, instance_id, False,
                          "Start aborted by user (database collision)")

    # ------------------------------------------- async confirm helper (UI flows)
    def _confirm_async(self, heading, body, confirm_label, callback,
                       destructive=False, extra_child=None):
        """Show a dialog on the main thread; callback(confirmed: bool).

        Used by UI-initiated flows (remove/discover/untrack) — unlike
        _ask_blocking, which serves core-driven confirm callbacks in bg threads.
        """
        def _done(confirmed):
            try:
                callback(confirmed)
            except Exception:
                pass

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=heading, body=body)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", confirm_label)
            if destructive:
                dlg.set_response_appearance(
                    "ok", Adw.ResponseAppearance.DESTRUCTIVE)
            else:
                dlg.set_response_appearance(
                    "ok", Adw.ResponseAppearance.SUGGESTED)
            if extra_child is not None:
                dlg.set_extra_child(extra_child)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _done(_finish_alert(d, t, "cancel") == "ok"))
        elif HAS_ADW and hasattr(Adw, "MessageDialog"):
            dlg = Adw.MessageDialog(transient_for=self,
                                    heading=heading, body=body)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", confirm_label)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.connect("response", lambda d, r: _done(r == "ok"))
            dlg.present()
        else:
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=(Gtk.MessageType.WARNING if destructive
                              else Gtk.MessageType.QUESTION),
                buttons=Gtk.ButtonsType.NONE, text=heading)
            dlg.format_secondary_text(body)
            dlg.add_button("Cancel", 0)
            dlg.add_button(confirm_label, 1)
            if extra_child is not None:
                try:
                    dlg.get_message_area().append(extra_child)
                except Exception:
                    pass
            dlg.connect("response", lambda d, r: (_done(r == 1), d.close()))
            dlg.present()

    # ------------------------------------------------- Sprint 4 flows
    def _set_primary_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import is_valid_identifier

        self._op_start()

        if not db_name or not is_valid_identifier(db_name):
            self._select_row(instance_id)
            if self.detail.instance_id == instance_id:
                self.detail.show_error(
                    "Pick a valid database name first (or choose Other… and type one).")
            self._op_end()
            return

        def _work() -> None:
            res = update_instance(instance_id, primary_db=db_name)
            GLib.idle_add(self._show_result, instance_id, res.ok,
                          f"Primary database set to '{db_name}'" if res.ok
                          else res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _switch_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import is_valid_identifier

        if not db_name or not is_valid_identifier(db_name):
            self._select_row(instance_id)
            if self.detail.instance_id == instance_id:
                self.detail.show_error("Pick a valid database name to switch to.")
            return
        self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            res = process_manager.switch_database(
                instance_id, db_name, confirm_cb=self._confirm_cb)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _track_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import track_database

        self._op_start()

        def _work() -> None:
            res = track_database(instance_id, db_name)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _untrack_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import untrack_database

        inst = get_instance(instance_id)
        if inst is not None and db_name == (inst.primary_db or ""):
            self._confirm_async(
                "Untrack primary database?",
                f"'{db_name}' is the current primary database. Untracking "
                "won't change what's running, but it will disappear from "
                "the switch list. Continue?",
                "Untrack", lambda ok: self._do_untrack(instance_id, db_name)
                if ok else None)
            return
        self._do_untrack(instance_id, db_name)

    def _do_untrack(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import untrack_database

        self._op_start()

        def _work() -> None:
            res = untrack_database(instance_id, db_name)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _discover_flow(self, instance_id: str) -> None:
        from odoo_vite.core import db_manager

        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            pw = None
            try:
                from odoo_vite.core.registry import get_db_password

                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            res = db_manager.list_databases_for_user(inst.db_user, pw)
            GLib.idle_add(self._show_discover_dialog, instance_id,
                          res.ok, res.message,
                          list((res.data or {}).get("databases", [])))

        threading.Thread(target=_work, daemon=True).start()

    def _show_discover_dialog(self, instance_id: str, ok: bool,
                              message: str, databases: list) -> bool:
        from odoo_vite.core.db_manager import track_database

        inst = get_instance(instance_id)
        if inst is None:
            self._op_end()
            return False
        if not ok:
            self._show_result(instance_id, False, message)
            return False
        tracked = set(inst.tracked_dbs or [])
        fresh = [d for d in databases if d not in tracked]
        if not fresh:
            self.toast("No new databases — everything found is tracked")
            self._op_end()
            return False

        # H-P1: searchable + scrollable checklist (client-side filter).
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search = Gtk.SearchEntry(placeholder_text="Filter databases…")
        outer.append(search)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        scrolled.set_max_content_height(380)
        outer.append(scrolled)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled.set_child(listbox)
        checks: dict[str, Gtk.CheckButton] = {}
        for name in fresh[:200]:
            check = Gtk.CheckButton(label=name, active=True)
            checks[name] = check
            listbox.append(check)
        if len(fresh) > 200:
            lbl = Gtk.Label(xalign=0)
            lbl.set_text(f"…and {len(fresh) - 200} more (track by name instead)")
            lbl.add_css_class("dim-label")
            listbox.append(lbl)

        def _apply_filter(_entry=None) -> None:
            filter_checks(checks, search.get_text() or "")

        search.connect("search-changed", _apply_filter)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                self._op_end()
                return

            def _work() -> None:
                picked = [n for n, c in checks.items() if c.get_active()]
                for name in picked:
                    track_database(instance_id, name)
                GLib.idle_add(self._show_result, instance_id, True,
                              f"Tracked {len(picked)} database(s)" if picked
                              else "Nothing selected")
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading="Discover databases",
                body=f"Found {len(fresh)} untracked database(s) for "
                     f"user '{inst.db_user}'. Tick the ones to track:")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Track selected")
            dlg.set_response_appearance(
                "ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(outer)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            # Fallback: track everything found, no per-item choice.
            _on_ok(True)
        return False

    def _remove_flow(self, instance_id: str) -> None:
        from odoo_vite.core import removal

        inst = get_instance(instance_id)
        if inst is None:
            return
        self._select_row(instance_id)
        if (inst.mode or "managed").lower() == "adopted":
            self._confirm_async(
                f"Remove '{inst.name}' from Odoo Vite?",
                "This will remove the instance from Odoo Vite.\n\n"
                "Your files and database will NOT be touched.",
                "Remove",
                lambda ok: self._do_remove(instance_id, False) if ok else None)
            return

        # Managed: type-to-confirm + per-database drop checkboxes in one dialog.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text=f"Type '{inst.name}' to confirm")
        box.append(entry)
        box.append(Gtk.Label(label="Databases to drop (all unchecked by default):",
                             xalign=0))
        db_checks: dict[str, Gtk.CheckButton] = {}
        for db_name in (inst.tracked_dbs or []):
            check = Gtk.CheckButton(label=db_name + ("  (primary)" if db_name == inst.primary_db else ""))
            check.set_active(False)
            db_checks[db_name] = check
            box.append(check)
        if inst.primary_db and inst.primary_db not in db_checks:
            check = Gtk.CheckButton(label=f"{inst.primary_db}  (primary)")
            check.set_active(False)
            db_checks[inst.primary_db] = check
            box.append(check)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            if entry.get_text().strip() != inst.name:
                self.toast("Name did not match — removal cancelled")
                return
            picked = [name for name, check in db_checks.items() if check.get_active()]
            self._do_remove(instance_id, drop_db=inst.primary_db in picked,
                            drop_extra_dbs=[n for n in picked if n != inst.primary_db])

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading=f"Remove '{inst.name}'?",
                body=f"This permanently deletes the instance folder ({inst.path}) "
                     "and unregisters it. Tick databases above to DROP them too.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Remove permanently")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self._confirm_async(
                f"Remove '{inst.name}'?",
                f"Type-to-confirm unavailable here — press Remove to delete "
                f"{inst.path}. No database is dropped in this fallback.",
                "Remove", lambda ok: self._do_remove(instance_id, False)
                if ok else None, destructive=True)

    def _do_remove(self, instance_id: str, drop_db: bool,
                   drop_extra_dbs: list | None = None) -> None:
        from odoo_vite.core import removal

        self._op_start()

        def _work() -> None:
            res = removal.remove_instance(instance_id, drop_db=drop_db,
                                          drop_extra_dbs=drop_extra_dbs)
            GLib.idle_add(self._after_remove, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _after_remove(self, instance_id: str, ok: bool, message: str) -> bool:
        self._op_end()
        if ok:
            self.toast(message)
            if self.detail.instance_id == instance_id:
                self.detail.show_instance(None)
        else:
            if self.detail.instance_id == instance_id:
                self.detail.show_error(message)
            else:
                self.toast(message)
        self.sidebar.full_refresh()
        self._poll_tick()
        return False

    def _refresh_detail_dbs(self, instance_id: str) -> bool:
        if self.detail.instance_id == instance_id:
            try:
                self.detail.refresh_databases()
            except Exception:
                pass
        return False
