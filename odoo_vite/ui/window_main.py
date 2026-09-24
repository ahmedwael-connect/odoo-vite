"""Main window shell (Sprint 1 shell + Sprint 3 lifecycle wiring).

Owns: sidebar/detail layout, 2s get_statuses() polling (background thread,
in-place row updates), Start/Stop/Restart flows, first-start confirmation
and DB-collision dialogs, pill CSS. Uses libadwaita when available.
"""

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

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
from odoo_vite.ui.flows.dialogs import _finish_alert, bind_check_highlight  # noqa: E402
from odoo_vite.ui.flows.instance_lifecycle import InstanceLifecycleFlows  # noqa: E402
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
/* A.2: checked rows carry their own always-visible treatment (bold +
   accent), independent of theme hover rendering. */
.discover-picked { font-weight: bold; color: @accent_color; }
"""


# ------------------------------------------------ blocking dialog helpers
# NOTE: these block the calling thread waiting on a GTK dialog, so they must
# be called from a background thread (all lifecycle flows are). They marshal
# the dialog itself onto the main loop via GLib.idle_add.


def group_discover(entries: list, instance_version: str) -> dict:
    """Sprint 5 B.2 grouping (structure, never exclusion).

    entries: [{name, initialized, odoo_major}]. Returns
    {"likely": [...names], "other": [...], "plain": [...]}, each sorted.
    """
    me = (instance_version or "").strip()
    likely, other, plain = [], [], []
    for entry in entries:
        name = entry.get("name", "")
        if entry.get("initialized") and entry.get("odoo_major"):
            (likely if entry["odoo_major"] == me else other).append(name)
        else:
            plain.append(name)
    return {"likely": sorted(likely), "other": sorted(other),
            "plain": sorted(plain)}


def discover_defaults(groups: dict) -> dict:
    """A.1: only the 'likely' group arrives pre-checked.

    Other groups stay visible and tickable (nothing hidden) — just not
    pre-selected, matching what a user wants most of the time.
    """
    out = {}
    for name in groups.get("likely", []):
        out[name] = True
    for name in groups.get("other", []) + groups.get("plain", []):
        out[name] = False
    return out


class MainWindow(BaseWindow):  # type: ignore[misc]
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Odoo Vite")
        self.set_default_size(1000, 660)
        self._rpc_sessions: dict = {}
        self.lifecycle = InstanceLifecycleFlows(self)
        from odoo_vite.ui.flows.database_ops import DatabaseOpsFlows  # noqa: E402
        self.db_ops = DatabaseOpsFlows(self)
        from odoo_vite.ui.flows.module_ops import ModuleOpsFlows  # noqa: E402
        self.module_ops = ModuleOpsFlows(self)
        from odoo_vite.ui.flows.configuration import ConfigurationFlows  # noqa: E402
        self.config_flows = ConfigurationFlows(self)
        from odoo_vite.ui.flows.logs_monitoring import LogsMonitoringFlows  # noqa: E402
        self.logs_flows = LogsMonitoringFlows(self)
        self._shell_sessions: dict = {}
        self._shell_timer_id = 0
        self._dev_watches: dict = {}
        self._devmode_timer_id = 0

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
        # A.3: version under the title, sourced from core/version.py.
        # (Neither Gtk nor Adw HeaderBar has set_subtitle — a two-line
        # title widget is the supported equivalent.)
        try:
            from odoo_vite.core.version import __version__ as _app_version

            _title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            _title_lbl = Gtk.Label(label="Odoo Vite")
            _title_lbl.add_css_class("title")
            _ver_lbl = Gtk.Label(label=f"v{_app_version}")
            _ver_lbl.add_css_class("caption")
            _ver_lbl.add_css_class("dim-label")
            _title_box.append(_title_lbl)
            _title_box.append(_ver_lbl)
            header.set_title_widget(_title_box)
            self.header_version = f"v{_app_version}"
        except Exception:
            self.header_version = ""
        self.header_bar = header  # exposed for tests/smoke checks
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
        self.btn_events = Gtk.ToggleButton(label="Events")
        self.btn_events.set_tooltip_text("Show/hide the app event feed")
        self.btn_events.connect("toggled", self._on_events_toggled)
        if hasattr(header, "pack_end"):
            header.pack_end(self.btn_events)

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

        # B.6: collapsible app event feed (live view over audit.log).
        self.event_revealer = Gtk.Revealer(reveal_child=False)
        self.event_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        event_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        event_box.set_margin_start(12)
        event_box.set_margin_end(12)
        event_box.set_margin_top(4)
        event_box.set_margin_bottom(4)
        event_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        event_title = Gtk.Label(label="App events", xalign=0, hexpand=True)
        event_title.add_css_class("heading")
        event_head.append(event_title)
        btn_event_clear_view = Gtk.Button(label="Clear view")
        btn_event_clear_view.set_tooltip_text(
            "Clears the panel (audit.log on disk is untouched)")
        btn_event_clear_view.connect("clicked", self._on_event_clear_view)
        event_head.append(btn_event_clear_view)
        event_box.append(event_head)
        from gi.repository import Gio as _EventGio

        self.event_store = _EventGio.ListStore(item_type=Gtk.StringObject)
        event_factory = Gtk.SignalListItemFactory()
        event_factory.connect("setup", self._on_event_row_setup)
        event_factory.connect("bind", self._on_event_row_bind)
        event_list = Gtk.ListView(model=Gtk.NoSelection(model=self.event_store),
                                  factory=event_factory)
        event_scrolled = Gtk.ScrolledWindow()
        event_scrolled.set_min_content_height(120)
        event_scrolled.set_max_content_height(220)
        event_scrolled.set_child(event_list)
        event_box.append(event_scrolled)
        self._event_scrolled = event_scrolled
        self.event_revealer.set_child(event_box)
        outer.append(self.event_revealer)
        self._event_pending: list = []
        self._event_follower = None
        self._event_flush_id = 0

        self._poll_busy = False
        self._poll_id = GLib.timeout_add(POLL_MS, self._poll_tick)
        self.connect("close-request", self._on_close_request)
        GLib.idle_add(self._poll_tick_soon)

    # ------------------------------------------------- B.6 event feed
    def _on_events_toggled(self, btn) -> None:
        show = bool(btn.get_active())
        self.event_revealer.set_reveal_child(show)
        if show:
            self._event_start()
        else:
            self._event_stop()

    def _event_start(self) -> None:
        from odoo_vite.core import audit as audit_log
        from odoo_vite.core import log_tail

        self._event_stop()
        try:
            follower = log_tail.LogFollower(str(audit_log.audit_path()))
            tail = log_tail.read_last_n(str(audit_log.audit_path()), 100)
        except Exception:
            return
        self._event_follower = follower
        for line in tail:
            self._event_pending.append(line)
        follower.sync_to_end()
        self._event_flush_id = GLib.timeout_add(300, self._event_flush_tick)
        GLib.timeout_add(1000, self._event_poll_tick)

    def _event_stop(self) -> None:
        if self._event_flush_id:
            try:
                GLib.source_remove(self._event_flush_id)
            except Exception:
                pass
            self._event_flush_id = 0
        self._event_follower = None
        self._event_pending = []

    def _event_poll_tick(self) -> bool:
        # Runs only while the panel is open (checked each tick).
        if not self.btn_events.get_active():
            return False
        follower = self._event_follower
        if follower is None:
            return True
        try:
            batch = follower.poll()
        except Exception:
            return True
        self._event_pending.extend(batch.get("lines", []))
        return True

    def _event_flush_tick(self) -> bool:
        # Debounced batching: bursts render at most every ~300ms, never per event.
        if not self.btn_events.get_active():
            return False
        pending, self._event_pending = self._event_pending, []
        for line in pending[-500:]:
            self.event_store.append(Gtk.StringObject.new(line[:300]))
        over = self.event_store.get_n_items() - 1000
        if over > 0:
            self.event_store.splice(0, over, [])
        try:
            adj = self._event_scrolled.get_vadjustment()
            if adj is not None:
                adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        except Exception:
            pass
        return True

    def _on_event_clear_view(self, _btn) -> None:
        self.event_store.splice(0, self.event_store.get_n_items(), [])
        self._event_pending = []

    def _on_event_row_setup(self, _factory, item) -> None:
        lbl = Gtk.Label(xalign=0, wrap=True)
        lbl.add_css_class("monospace")
        item.set_child(lbl)

    def _on_event_row_bind(self, _factory, item) -> None:
        obj = item.get_item()
        text = obj.get_string() if obj is not None else ""
        try:
            import json as _json

            entry = _json.loads(text)
            text = (f"{entry.get('ts', '')[:19]}  {entry.get('action', '')}  "
                    f"{entry.get('instance_name', '')}  {entry.get('detail', '')}")
        except Exception:
            pass
        if len(text) > 300:
            text = text[:300] + "…"
        try:
            item.get_child().set_text(text)
        except Exception:
            pass

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
        self.db_ops._refresh_detail_dbs(instance_id)
        self.module_ops._load_modules(instance_id)
        try:
            self.detail.set_devmode_state(
                instance_id in (self._dev_watches or {}),
                "watching" if instance_id in (self._dev_watches or {}) else "")
        except Exception:
            pass
        try:
            from odoo_vite.core import devtools_export

            self.detail.set_editors_visibility(devtools_export.detect_editors())
        except Exception:
            pass
        try:
            inst = get_instance(instance_id)
            self.detail.stop_log_poll()
            if inst is not None and inst.log_path:
                self.detail.start_log_poll(inst.log_path)
        except Exception:
            pass

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
    def _progress_dialog(self, title: str):
        # Sprint R shim: moved to flows.dialogs.build_progress_dialog; kept
        # until R.4/R.7 move the remaining callers, then deleted.
        from odoo_vite.ui.flows.dialogs import build_progress_dialog

        return build_progress_dialog(self, title)

    def _on_row_action(self, action: str, instance_id: str, payload=None) -> None:
        if action == "start":
            self.lifecycle.start_flow(instance_id)
        elif action in ("stop", "restart"):
            self.lifecycle._run_simple_flow(action, instance_id)
        elif action == "repair":
            self.lifecycle._repair_flow(instance_id)
        elif action == "set-primary":
            self.lifecycle._set_primary_flow(instance_id, (payload or "").strip())
        elif action == "switch":
            self.lifecycle._switch_flow(instance_id, (payload or "").strip())
        elif action == "track":
            self.lifecycle._track_flow(instance_id, (payload or "").strip())
        elif action == "untrack":
            self.lifecycle._untrack_flow(instance_id, (payload or "").strip())
        elif action == "discover":
            self.lifecycle._discover_flow(instance_id)
        elif action == "remove":
            self.lifecycle._remove_flow(instance_id)
        elif action == "secured":
            self.toast(str(payload or "Password secured"))
        elif action == "init-db":
            self.db_ops._init_db_flow(instance_id, (payload or "").strip())
        elif action == "backup-db":
            self.db_ops._backup_flow(instance_id, (payload or "").strip())
        elif action == "drop-db":
            self.db_ops._drop_db_flow(instance_id, (payload or "").strip())
        elif action == "restore":
            self.db_ops._restore_flow(instance_id)
        elif action == "validate":
            self.db_ops._validate_flow(instance_id)
        elif action == "refresh-states":
            self.db_ops._refresh_detail_dbs(instance_id)
        elif action == "conf-save":
            self.config_flows._conf_save_flow(instance_id, payload or {})
        elif action == "conf-restore":
            self.config_flows._conf_restore_flow(instance_id)
        elif action == "conf-regenerate":
            self.config_flows._conf_regenerate_flow(instance_id)
        elif action == "meta-save":
            self.config_flows._meta_save_flow(instance_id, payload or {})
        elif action == "addons-manage":
            self.config_flows._addons_manage_dialog(instance_id)
        elif action == "mod-refresh":
            self.module_ops._load_modules(instance_id)
        elif action == "mod-install":
            self.module_ops._mod_install_flow(instance_id, payload or [])
        elif action == "mod-update":
            self.module_ops._mod_update_flow(instance_id, payload or [])
        elif action == "mod-update-code":
            self.module_ops._mod_update_code_flow(instance_id)
        elif action == "mod-uninstall":
            self.module_ops._mod_uninstall_flow(instance_id, str(payload or ""))
        elif action == "mod-deps":
            self.module_ops._mod_deps_dialog(instance_id)
        elif action == "mod-scaffold":
            self.module_ops._mod_scaffold_wizard(instance_id)
        elif action == "log-search":
            self.logs_flows._log_search_flow(instance_id)
        elif action == "log-doctor":
            self.logs_flows._log_doctor_flow(instance_id)
        elif action == "slow-refresh":
            self.logs_flows._slow_refresh_flow(instance_id)
        elif action == "profile":
            self.logs_flows._profile_flow(instance_id, payload or 10)
        elif action == "rpc-connect":
            self._rpc_connect_flow(instance_id)
        elif action == "model-selected":
            self._dev_model_selected(instance_id, str(payload or ""))
        elif action == "rec-search":
            self._rec_search_flow(instance_id)
        elif action == "rec-prev":
            self._rec_page_flow(instance_id, -1)
        elif action == "rec-next":
            self._rec_page_flow(instance_id, 1)
        elif action == "rec-new":
            self._rec_new_flow(instance_id)
        elif action == "rec-edit":
            self._rec_edit_flow(instance_id)
        elif action == "rec-delete":
            self._rec_delete_flow(instance_id)
        elif action == "cron-refresh":
            self._cron_refresh_flow(instance_id)
        elif action == "gen-launch":
            self._launch_json_flow(instance_id)
        elif action == "open-code":
            self._open_editor_flow(instance_id, "code")
        elif action == "open-cursor":
            self._open_editor_flow(instance_id, "cursor")
        elif action == "shell-start":
            self._shell_start_flow(instance_id)
        elif action == "shell-send":
            self._shell_send_flow(instance_id, str(payload or ""))
        elif action == "shell-stop":
            self._shell_stop_flow(instance_id)
        elif action == "devmode":
            self._devmode_flow(instance_id, bool(payload))
        elif action == "test-run":
            self._test_run_flow(instance_id)

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
                    self.detail.btn_remove, self.detail.btn_browser,
                    self.detail.btn_conf_save, self.detail.btn_conf_regen,
                    self.detail.btn_mod_install, self.detail.btn_mod_update,
                    self.detail.btn_mod_code):
            try:
                btn.set_sensitive(sensitive)
            except Exception:
                pass

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
    def _rpc_session(self, instance_id: str):
        return (self._rpc_sessions or {}).get(instance_id)

    def _rpc_connect_flow(self, instance_id: str) -> None:
        from odoo_vite.core import odoo_rpc

        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            user, password, remember = self.detail.rpc_credentials()
        except Exception:
            user, password, remember = "", "", False
        if not user:
            # keyring recall as a convenience before asking
            try:
                user, password = odoo_rpc.recall_credentials(instance_id)
            except Exception:
                user, password = "", "", 
            if user:
                try:
                    self.detail.entry_rpc_user.set_text(user)
                    self.detail.entry_rpc_pass.set_text(password)
                except Exception:
                    pass
        try:
            user, password, remember = self.detail.rpc_credentials()
        except Exception:
            user, password, remember = "", "", False
        self._select_row(instance_id)
        self._op_start()

        def _work() -> None:
            res = odoo_rpc.connect_instance(
                inst, odoo_user=user or None, odoo_password=password)
            if res.ok and remember and user:
                odoo_rpc.remember_credentials(instance_id, user, password)
            GLib.idle_add(self._show_rpc_connected, instance_id, res.ok,
                           res.message, dict(res.data or {}) if res.ok else {})

        threading.Thread(target=_work, daemon=True).start()

    def _show_rpc_connected(self, instance_id: str, ok: bool, message: str,
                            client: dict) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_rpc_status(message)
            except Exception:
                pass
        if ok:
            if self._rpc_sessions is None:
                self._rpc_sessions = {}
            self._rpc_sessions[instance_id] = client
            self.toast(message)
            self._dev_list_models(instance_id)
        return False

    def _dev_client(self, instance_id: str):
        client = (self._rpc_sessions or {}).get(instance_id)
        if client is None and self.detail.instance_id == instance_id:
            try:
                self.detail.set_rpc_status(
                    "Not connected — enter Odoo credentials and Connect first.")
            except Exception:
                pass
        return client

    def _dev_list_models(self, instance_id: str) -> None:
        client = self._rpc_session(instance_id)
        if client is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.list_models(client)
            GLib.idle_add(self._show_models, instance_id, res.ok,
                           res.data.get("models", []) if res.ok else [],
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_models(self, instance_id: str, ok: bool, models: list,
                     error: str) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                if ok:
                    self.detail.render_model_rows(models)
                else:
                    self.detail.set_rpc_status(error)
            except Exception:
                pass
        return False

    def _dev_model_selected(self, instance_id: str, model: str) -> None:
        if not model:
            return
        client = self._dev_client(instance_id)
        if client is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.get_model_metadata(client, model)
            GLib.idle_add(self._show_metadata, instance_id, res.ok,
                           res.data if res.ok else {},
                           "" if res.ok else res.message)
            if res.ok:
                GLib.idle_add(self._dev_records_page, instance_id, model, 0)

        threading.Thread(target=_work, daemon=True).start()

    def _show_metadata(self, instance_id: str, ok: bool, meta: dict,
                       error: str) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                if ok:
                    self.detail.set_model_metadata(meta)
                else:
                    self.detail.set_rpc_status(error)
            except Exception:
                pass
        return False

    # --------------------------------------------------- records
    def _rec_domain(self):
        try:
            field = (self.detail.entry_dom_field.get_text() or "").strip()
            item = self.detail.drop_dom_op.get_selected_item()
            op = item.get_string() if item is not None else "="
            value = (self.detail.entry_dom_value.get_text() or "").strip()
        except Exception:
            return []
        if not field:
            return []
        return [[field, op, value]]

    def _dev_records_page(self, instance_id: str, model: str, offset: int) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.search_records(
                client, model, domain=self._rec_domain(),
                fields=["id", "display_name", "name"], offset=offset, limit=50)
            GLib.idle_add(self._show_records, instance_id,
                           res.ok, res.data if res.ok else [],
                           offset, model,
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_records(self, instance_id: str, ok: bool, records: list,
                      offset: int, model: str, error: str) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                if ok:
                    self.detail.set_records(records, offset, 50, model)
                else:
                    self.detail.set_rpc_status(error)
            except Exception:
                pass
        return False

    def _rec_search_flow(self, instance_id: str) -> None:
        model = ""
        try:
            model = self.detail.selected_model() or self.detail._records_model
        except Exception:
            pass
        if not model:
            self.toast("Pick a model in the inspector first")
            return
        self._dev_records_page(instance_id, model, 0)

    def _rec_page_flow(self, instance_id: str, delta: int) -> None:
        try:
            model = self.detail._records_model
            offset = max(0, self.detail._records_offset + delta * 50)
        except Exception:
            return
        if not model:
            return
        self._dev_records_page(instance_id, model, offset)

    def _rec_new_flow(self, instance_id: str) -> None:
        try:
            model = self.detail.selected_model() or self.detail._records_model
        except Exception:
            model = ""
        if not model:
            self.toast("Pick a model in the inspector first")
            return
        self._record_edit_dialog(instance_id, model, None, {})

    def _rec_edit_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        try:
            model = self.detail._records_model
            record_id = self.detail.selected_record()
        except Exception:
            return
        if not model or not record_id:
            self.toast("Select a record first")
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.search_records(
                client, model, domain=[["id", "=", record_id]], limit=1)
            GLib.idle_add(self._show_edit_dialog, instance_id, model,
                           record_id, res.ok,
                           (res.data[0] if res.ok and res.data else {}),
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_edit_dialog(self, instance_id: str, model: str, record_id,
                          ok: bool, record: dict, error: str) -> bool:
        self._op_end()
        if not ok:
            self.toast(f"Cannot read record: {error[:160]}")
            return False
        self._record_edit_dialog(instance_id, model, record_id, record or {})
        return False

    @staticmethod
    def _coerce_value(text: str):
        low = text.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(text.strip())
        except (ValueError, TypeError):
            pass
        try:
            return float(text.strip())
        except (ValueError, TypeError):
            pass
        return text

    def _record_edit_dialog(self, instance_id: str, model: str, record_id,
                            record: dict) -> None:
        is_new = record_id is None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rows: list = []

        def _add_row(key="", value=""):
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            key_entry = Gtk.Entry(text=key, placeholder_text="field name")
            key_entry.set_size_request(140, -1)
            val_entry = Gtk.Entry(text=value, hexpand=True,
                                  placeholder_text="value")
            hbox.append(key_entry)
            hbox.append(val_entry)
            box.append(hbox)
            rows.append((key_entry, val_entry))

        if is_new:
            _add_row("name", "")
        else:
            for key, value in (record or {}).items():
                if key == "id" or key.startswith("_"):
                    continue
                if isinstance(value, (list, dict)):
                    continue  # relational blobs don't round-trip as text
                _add_row(key, "" if value is False else str(value if value is not None else ""))
        btn_more = Gtk.Button(label="+ Add field")
        btn_more.connect("clicked", lambda _b: _add_row())
        box.append(btn_more)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            values = {}
            for key_entry, val_entry in rows:
                key = (key_entry.get_text() or "").strip()
                if not key:
                    continue
                values[key] = self._coerce_value(val_entry.get_text() or "")
            if not values:
                self.toast("No field values given")
                return
            if is_new:
                self._op_start()

                def _work() -> None:
                    from odoo_vite.core import odoo_inspect

                    client = self._dev_client(instance_id)
                    if client is None:
                        GLib.idle_add(self._op_end)
                        return
                    res = odoo_inspect.create_record(client, model, values)
                    GLib.idle_add(self._show_result, instance_id, res.ok,
                                   res.message)
                    if res.ok:
                        GLib.idle_add(self._dev_records_page, instance_id,
                                       model, 0)

                threading.Thread(target=_work, daemon=True).start()
                return
            # update: explicit changed-fields preview before commit
            current = record or {}
            changed = diff_record_values(current, values)
            if not changed:
                self.toast("No changes vs current values")
                return
            preview = "\n".join(f"{k}: {old!r} → {new!r}"
                                for k, (old, new) in changed.items())
            only = {k: new for k, (old, new) in changed.items()}

            def _commit(ok2: bool) -> None:
                if not ok2:
                    return
                self._op_start()

                def _work2() -> None:
                    from odoo_vite.core import odoo_inspect

                    client = self._dev_client(instance_id)
                    if client is None:
                        GLib.idle_add(self._op_end)
                        return
                    res = odoo_inspect.update_record(client, model, record_id,
                                                     only)
                    GLib.idle_add(self._show_result, instance_id, res.ok,
                                   res.message)
                    if res.ok:
                        try:
                            off = self.detail._records_offset
                        except Exception:
                            off = 0
                        GLib.idle_add(self._dev_records_page, instance_id,
                                       model, off)

                threading.Thread(target=_work2, daemon=True).start()

            if HAS_ADW and HAS_ALERT:
                dlg2 = Adw.AlertDialog(
                    heading=f"Update {model} #{record_id}?",
                    body="Changed fields:\n" + preview[:1500])
                dlg2.add_response("cancel", "Cancel")
                dlg2.add_response("ok", "Apply update")
                dlg2.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
                dlg2.set_default_response("cancel")
                dlg2.set_close_response("cancel")
                dlg2.choose(self, None,
                            lambda d, t: _commit(_finish_alert(d, t, "cancel") == "ok"))
            else:
                _commit(True)

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading=f"{'New' if is_new else 'Edit'} {model}"
                        + ("" if is_new else f" #{record_id}"),
                body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Create" if is_new else "Review update")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.toast("Record editor needs libadwaita dialogs")

    def _rec_delete_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        try:
            model = self.detail._records_model
            record_id = self.detail.selected_record()
        except Exception:
            return
        if not model or not record_id:
            self.toast("Select a record first")
            return
        label = ""
        try:
            for rec in self.detail._records_cache:
                if rec.get("id") == record_id:
                    label = f"{model} '{rec.get('display_name') or rec.get('name', record_id)}'"
                    break
        except Exception:
            pass
        label = label or f"{model} #{record_id}"
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(
            label=f"This permanently deletes {label}. Odoo itself may refuse "
                  "when dependents block it — whatever it reports is shown "
                  "verbatim. There is no undo.",
            xalign=0, wrap=True))
        entry = Gtk.Entry(placeholder_text=f"Type the record label to confirm")
        box.append(entry)
        expected = ""
        try:
            for rec in self.detail._records_cache:
                if rec.get("id") == record_id:
                    expected = str(rec.get("display_name") or rec.get("name", ""))
                    break
        except Exception:
            pass
        if not expected:
            expected = str(record_id)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            if (entry.get_text() or "").strip() != expected:
                self.toast("Label did not match — delete cancelled")
                return
            self._op_start()

            def _work() -> None:
                from odoo_vite.core import odoo_inspect

                res = odoo_inspect.delete_record(client, model, record_id,
                                                 expected)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                if res.ok:
                    try:
                        off = self.detail._records_offset
                    except Exception:
                        off = 0
                    GLib.idle_add(self._dev_records_page, instance_id, model, off)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=f"Delete {label}?", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Delete permanently")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.toast("Delete needs libadwaita dialogs")

    # --------------------------------------------------- cron/export
    def _cron_refresh_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.list_cron_jobs(client)
            GLib.idle_add(self._show_crons, instance_id, res.ok,
                           res.data.get("crons", []) if res.ok else [],
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_crons(self, instance_id: str, ok: bool, crons: list,
                    error: str) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                if ok:
                    self.detail.set_crons(crons)
                else:
                    self.detail.set_rpc_status(error)
            except Exception:
                pass
        return False

    def _launch_json_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import devtools_export

            res = devtools_export.generate_launch_json(inst)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _open_editor_flow(self, instance_id: str, editor: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import devtools_export

            res = devtools_export.open_in_editor(editor, inst.path)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    # ----------------------------------- Sprint 11 live control
    def _shell_session(self, instance_id: str):
        return (self._shell_sessions or {}).get(instance_id)

    def _shell_start_flow(self, instance_id: str) -> None:
        from odoo_vite.core import odoo_shell

        inst = get_instance(instance_id)
        if inst is None:
            return
        self._select_row(instance_id)
        self._op_start()
        if self._shell_sessions is None:
            self._shell_sessions = {}

        def _work() -> None:
            session = odoo_shell.OdooShell()
            res = session.start(inst)
            GLib.idle_add(self._show_shell_started, instance_id, res.ok,
                           res.message, session if res.ok else None)

        threading.Thread(target=_work, daemon=True).start()

    def _show_shell_started(self, instance_id: str, ok: bool, message: str,
                            session) -> bool:
        self._op_end()
        if ok and session is not None:
            self._shell_sessions[instance_id] = session
            self._shell_timer_ensure()
        if self.detail.instance_id == instance_id:
            try:
                self.detail.shell_set_status(message)
            except Exception:
                pass
        if not ok:
            self.toast(f"Shell failed: {message[:200]}")
        return False

    def _shell_timer_ensure(self) -> None:
        if not getattr(self, "_shell_timer_id", 0):
            self._shell_timer_id = GLib.timeout_add(200, self._shell_poll_tick)

    def _shell_poll_tick(self) -> bool:
        live = False
        for instance_id, session in list((self._shell_sessions or {}).items()):
            try:
                lines = session.drain_output()
            except Exception:
                lines = []
            if lines and self.detail.instance_id == instance_id:
                try:
                    self.detail.shell_append(lines)
                except Exception:
                    pass
            try:
                alive = session.running
            except Exception:
                alive = False
            if alive:
                live = True
            else:
                try:
                    code = session.exit_code
                except Exception:
                    code = "?"
                if self.detail.instance_id == instance_id:
                    try:
                        self.detail.shell_set_status(
                            f"Shell exited (code {code}) — Start again if needed.")
                    except Exception:
                        pass
                try:
                    del self._shell_sessions[instance_id]
                except KeyError:
                    pass
        if not live:
            self._shell_timer_id = 0
            return False
        return True

    def _shell_send_flow(self, instance_id: str, text: str) -> None:
        session = self._shell_session(instance_id)
        if session is None:
            self.toast("Start the shell first")
            return

        def _work() -> None:
            try:
                res = session.send_line(text or "")
            except Exception as exc:
                GLib.idle_add(self.toast, f"Shell send failed: {exc}")
                return
            if not res.ok:
                GLib.idle_add(self.toast, f"Shell send failed: {res.message[:160]}")

        threading.Thread(target=_work, daemon=True).start()

    def _shell_stop_flow(self, instance_id: str) -> None:
        session = self._shell_session(instance_id)
        if session is None:
            return

        def _work() -> None:
            try:
                res = session.stop()
            except Exception as exc:
                GLib.idle_add(self.toast, f"Shell stop failed: {exc}")
                return
            GLib.idle_add(self.toast, res.message if res.ok else
                          f"Shell stop failed: {res.message[:160]}")

        threading.Thread(target=_work, daemon=True).start()

    # --------------------------------------------------- dev mode
    def _devmode_flow(self, instance_id: str, on: bool) -> None:
        if on:
            self._devmode_start(instance_id)
        else:
            self._devmode_stop(instance_id, announce=True)

    def _devmode_start(self, instance_id: str) -> None:
        from odoo_vite.core import devwatch
        from odoo_vite.core.process_manager import _alive_pid

        inst = get_instance(instance_id)
        if inst is None:
            return
        self._select_row(instance_id)
        if self._dev_watches is None:
            self._dev_watches = {}
        if instance_id in self._dev_watches:
            return  # already watching

        def _prep() -> None:
            # Toggling on while stopped starts the instance (explicit, logged).
            live = _alive_pid(inst) is not None
            if not live:
                res = self._devmode_start_process(inst)
                if res is not None:
                    GLib.idle_add(self._show_result, instance_id, False, res)
                    GLib.idle_add(self._sync_devmode_toggle, instance_id, False,
                                   "could not start")
                    return
            GLib.idle_add(self._devmode_attach, instance_id)

        threading.Thread(target=_prep, daemon=True).start()

    def _devmode_start_process(self, inst):
        """Start the instance for dev mode. Returns None on success or an
        error message (runs in a background thread)."""
        from odoo_vite.core import process_manager

        res = process_manager.start_instance(inst.id)
        return None if res.ok else res.message

    def _devmode_attach(self, instance_id: str) -> bool:
        from gi.repository import Gio as _Gio

        from odoo_vite.core import devwatch

        inst = get_instance(instance_id)
        if inst is None:
            return False
        roots = devwatch.watch_roots(inst)
        if not roots:
            self.toast("Dev Mode needs at least one addons folder on disk")
            self._sync_devmode_toggle(instance_id, False, "no folders")
            return False
        controller = devwatch.DebounceController()
        monitors = []
        try:
            for root in roots:
                monitors.extend(self._monitor_tree(root, controller))
        except Exception as exc:
            self.toast(f"Dev Mode failed to watch: {exc}")
            self._sync_devmode_toggle(instance_id, False, "watch failed")
            return False
        self._dev_watches[instance_id] = {
            "controller": controller, "monitors": monitors}
        if not getattr(self, "_devmode_timer_id", 0):
            self._devmode_timer_id = GLib.timeout_add(
                250, self._devmode_tick)
        try:
            self.detail.set_devmode_state(True, f"watching {len(roots)} folder(s)")
        except Exception:
            pass
        self.toast("Dev Mode on — saving watched files restarts the instance")
        return False

    def _monitor_tree(self, root: str, controller) -> list:
        """Attach Gio.FileMonitors recursively (dirs only). Returns handles."""
        from gi.repository import Gio as _Gio

        from odoo_vite.core import devwatch

        handles = []

        def _attach(directory: str) -> None:
            try:
                gfile = _Gio.File.new_for_path(directory)
                monitor = gfile.monitor_directory(_Gio.FileMonitorFlags.NONE,
                                                  None)
            except Exception:
                return

            def _changed(_mon, _file, _other, _event):
                try:
                    path = _file.get_path() if _file is not None else ""
                except Exception:
                    path = ""
                if not path:
                    return
                # new subdirectories join the watch on creation
                if _event in (_Gio.FileMonitorEvent.CREATED,):
                    try:
                        import os as _os

                        if _os.path.isdir(path):
                            _attach(path)
                    except Exception:
                        pass
                try:
                    if devwatch.should_watch(path):
                        controller.feed()
                except Exception:
                    pass

            monitor.connect("changed", _changed)
            handles.append(monitor)

        _attach(root)
        try:
            import os as _os

            for dirpath, dirnames, _files in _os.walk(root):
                # Consistent with devwatch.should_watch: only well-known junk
                # dirs are pruned; dot-parents like ~/.local must stay visible.
                dirnames[:] = [d for d in dirnames if d not in devwatch.SKIP_DIRS]
                for dirname in dirnames:
                    _attach(_os.path.join(dirpath, dirname))
        except Exception:
            pass
        return handles

    def _devmode_tick(self) -> bool:
        if not self._dev_watches:
            self._devmode_timer_id = 0
            return False

        def _fire(instance_id: str):
            def _work() -> None:
                from odoo_vite.core import audit as _audit
                from odoo_vite.core import process_manager

                _audit.log_event(instance_id, "", "dev_restart",
                                 "dev-mode file change — restarting")
                res = process_manager.restart_instance(instance_id)
                GLib.idle_add(self._show_result, instance_id, res.ok,
                              ("Dev-mode auto-restart: " + res.message) if res.ok
                              else ("Dev-mode restart failed: " + res.message))

            threading.Thread(target=_work, daemon=True).start()

        for instance_id, watch in list(self._dev_watches.items()):
            try:
                watch["controller"].check(lambda: _fire(instance_id))
            except Exception:
                pass
        return True

    def _devmode_stop(self, instance_id: str, announce: bool = False) -> None:
        watches = self._dev_watches or {}
        watch = watches.pop(instance_id, None)
        if watch is not None:
            for monitor in watch.get("monitors", []):
                try:
                    monitor.cancel()
                except Exception:
                    pass
        if not watches and getattr(self, "_devmode_timer_id", 0):
            try:
                GLib.source_remove(self._devmode_timer_id)
            except Exception:
                pass
            self._devmode_timer_id = 0
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_devmode_state(False, "")
            except Exception:
                pass
        if announce:
            self.toast("Dev Mode off (instance left running)")

    def _sync_devmode_toggle(self, instance_id: str, on: bool,
                             note: str = "") -> bool:
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_devmode_state(on, note)
            except Exception:
                pass
        return False

    # --------------------------------------------------- test runner
    def _test_run_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            module, target = self.detail.test_fields()
        except Exception:
            module, target = "", ""
        module, target = (module or "").strip(), (target or "").strip()
        if not module:
            self.toast("Enter the module technical name to test")
            return
        if not target:
            target = f"{(inst.primary_db or 'odoo').strip()}_test"
        if target == (inst.primary_db or "").strip():
            self.toast("Refusing to run tests on the PRIMARY database — "
                       "pick a disposable test database")
            return
        self._select_row(instance_id)

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()
            dlg, append, done = self._progress_dialog(
                f"Tests: {module} on {target}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.run_module_tests(
                    inst, target, module, progress_cb=_feed)
                summary = (res.data or {}).get("summary", {})
                status = summary.get("status", "?")
                GLib.idle_add(done, res.ok and status == "passed",
                              f"{res.message} | tests: {summary.get('ran', '?')} "
                              f"ran, status={status}")
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)

            threading.Thread(target=_work, daemon=True).start()

        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {target} "
               f"-i/-u {module} --test-enable --stop-after-init")
        # Honest command preview: -i vs -u depends on target existence at run
        # time (missing → create+install, existing → update+test).
        self.module_ops._confirm_command(
            f"Run {module} tests on '{target}'?",
            f"{cmd}\n\nTarget is disposable (never the primary). Missing DBs "
            "are created+installed (-i); existing ones are updated+tested (-u).",
            "Run tests", _go)
