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
        from odoo_vite.ui.flows.dev_tools_process import DevToolsProcessFlows  # noqa: E402
        from odoo_vite.ui.flows.dev_tools_rpc import DevToolsRpcFlows  # noqa: E402
        self.dev_rpc = DevToolsRpcFlows(self)
        self.dev_proc = DevToolsProcessFlows(self)
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
            self.dev_rpc._rpc_connect_flow(instance_id)
        elif action == "model-selected":
            self.dev_rpc._dev_model_selected(instance_id, str(payload or ""))
        elif action == "rec-search":
            self.dev_rpc._rec_search_flow(instance_id)
        elif action == "rec-prev":
            self.dev_rpc._rec_page_flow(instance_id, -1)
        elif action == "rec-next":
            self.dev_rpc._rec_page_flow(instance_id, 1)
        elif action == "rec-new":
            self.dev_rpc._rec_new_flow(instance_id)
        elif action == "rec-edit":
            self.dev_rpc._rec_edit_flow(instance_id)
        elif action == "rec-delete":
            self.dev_rpc._rec_delete_flow(instance_id)
        elif action == "cron-refresh":
            self.dev_rpc._cron_refresh_flow(instance_id)
        elif action == "gen-launch":
            self.dev_rpc._launch_json_flow(instance_id)
        elif action == "open-code":
            self.dev_rpc._open_editor_flow(instance_id, "code")
        elif action == "open-cursor":
            self.dev_rpc._open_editor_flow(instance_id, "cursor")
        elif action == "shell-start":
            self.dev_proc._shell_start_flow(instance_id)
        elif action == "shell-send":
            self.dev_proc._shell_send_flow(instance_id, str(payload or ""))
        elif action == "shell-stop":
            self.dev_proc._shell_stop_flow(instance_id)
        elif action == "devmode":
            self.dev_proc._devmode_flow(instance_id, bool(payload))
        elif action == "test-run":
            self.dev_proc._test_run_flow(instance_id)

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
