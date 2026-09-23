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


def bind_check_highlight(check) -> None:
    """A.2: mirror a CheckButton's state into an always-visible style.

    Root cause investigated: Gtk.CheckButton state binding itself is correct
    (constructor + set_active verified); the reported ambiguity is theme
    rendering. This adds a deterministic treatment (bold + accent label via
    .discover-picked) driven off the toggled signal, so selected rows read
    clearly in any theme with or without hovering.
    """

    def _sync(*_args) -> None:
        try:
            active = bool(check.get_active())
        except Exception:
            return
        try:
            if active:
                check.add_css_class("discover-picked")
            else:
                check.remove_css_class("discover-picked")
        except Exception:
            pass

    try:
        check.connect("toggled", _sync)
    except Exception:
        pass
    _sync()


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
        self._refresh_detail_dbs(instance_id)
        self._load_modules(instance_id)
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
        elif action == "init-db":
            self._init_db_flow(instance_id, (payload or "").strip())
        elif action == "backup-db":
            self._backup_flow(instance_id, (payload or "").strip())
        elif action == "drop-db":
            self._drop_db_flow(instance_id, (payload or "").strip())
        elif action == "restore":
            self._restore_flow(instance_id)
        elif action == "validate":
            self._validate_flow(instance_id)
        elif action == "refresh-states":
            self._refresh_detail_dbs(instance_id)
        elif action == "conf-save":
            self._conf_save_flow(instance_id, payload or {})
        elif action == "conf-restore":
            self._conf_restore_flow(instance_id)
        elif action == "conf-regenerate":
            self._conf_regenerate_flow(instance_id)
        elif action == "meta-save":
            self._meta_save_flow(instance_id, payload or {})
        elif action == "addons-manage":
            self._addons_manage_dialog(instance_id)
        elif action == "mod-refresh":
            self._load_modules(instance_id)
        elif action == "mod-install":
            self._mod_install_flow(instance_id, payload or [])
        elif action == "mod-update":
            self._mod_update_flow(instance_id, payload or [])
        elif action == "mod-update-code":
            self._mod_update_code_flow(instance_id)
        elif action == "mod-uninstall":
            self._mod_uninstall_flow(instance_id, str(payload or ""))
        elif action == "mod-deps":
            self._mod_deps_dialog(instance_id)
        elif action == "mod-scaffold":
            self._mod_scaffold_wizard(instance_id)
        elif action == "log-search":
            self._log_search_flow(instance_id)
        elif action == "log-doctor":
            self._log_doctor_flow(instance_id)
        elif action == "slow-refresh":
            self._slow_refresh_flow(instance_id)
        elif action == "profile":
            self._profile_flow(instance_id, payload or 10)
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
            from odoo_vite.core.db_state import get_db_state, odoo_major
            from odoo_vite.core.registry import get_db_password

            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            res = db_manager.list_databases_for_user(inst.db_user, pw)
            if not res.ok:
                GLib.idle_add(self._show_result, instance_id, False, res.message)
                return
            tracked = set(inst.tracked_dbs or [])
            entries = []
            for name in (res.data or {}).get("databases", []):
                if name in tracked:
                    continue
                try:
                    st = get_db_state(name, inst.db_user, pw)
                    entries.append({"name": name,
                                    "initialized": st.initialized,
                                    "odoo_major": odoo_major(st.odoo_version)})
                except Exception:
                    entries.append({"name": name, "initialized": False,
                                    "odoo_major": ""})
            GLib.idle_add(self._show_discover_dialog, instance_id,
                           entries, inst.version or "")

        threading.Thread(target=_work, daemon=True).start()

    def _show_discover_dialog(self, instance_id: str, entries: list,
                              instance_version: str) -> bool:
        from odoo_vite.core.db_manager import track_database

        inst = get_instance(instance_id)
        if inst is None:
            self._op_end()
            return False
        groups = group_discover(entries, instance_version)
        total = sum(len(v) for v in groups.values())
        if total == 0:
            self.toast("No new databases — everything found is tracked")
            self._op_end()
            return False

        # H-P1: searchable + scrollable checklist (client-side filter).
        # B.2 grouping (structure, never exclusion) + H-P1 search/scroll.
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

        def _section(title: str) -> None:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.add_css_class("heading")
            listbox.append(lbl)

        shown = 0
        defaults = discover_defaults(groups)
        sections = (
            # A.1: only "likely" arrives pre-checked (see discover_defaults).
            (f"Likely Odoo {instance_version or '?'}", groups["likely"], None),
            ("Other Odoo databases", groups["other"], None),
            ("Uninitialized / non-Odoo", groups["plain"], "expand"),
        )
        for title, names, collapsed in sections:
            if not names:
                continue
            container = listbox
            if collapsed:
                expander = Gtk.Expander(
                    label=f"{title} ({len(names)}) — click to expand",
                    expanded=False)
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                expander.set_child(inner)
                listbox.append(expander)
                container = inner
            else:
                _section(f"{title} ({len(names)})")
            for name in names[:200]:
                check = Gtk.CheckButton(label=name,
                                        active=defaults.get(name, False))
                bind_check_highlight(check)
                checks[name] = check
                container.append(check)
                shown += 1
        if total > shown:
            lbl = Gtk.Label(xalign=0)
            lbl.set_text(f"…and {total - shown} more (track by name instead)")
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
                body=f"Grouped by likely relevance to Odoo {instance_version or '?'} "
                     f"for user '{inst.db_user}' — nothing is hidden, tick what to track:")
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

    # --------------------------------------- Sprint 5 Database Operations
    def _init_db_flow(self, instance_id: str, db_name: str) -> None:
        self._op_start()
        self._select_row(instance_id)

        def _work() -> None:
            res = process_manager.initialize_database(instance_id, db_name)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _backup_flow(self, instance_id: str, db_name: str) -> None:
        from datetime import datetime, timezone

        inst = get_instance(instance_id)
        if inst is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        default = f"{db_name}_{stamp}.dump"
        if not hasattr(Gtk, "FileDialog"):
            self.toast("File picker unavailable on this GTK version")
            return
        dlg = Gtk.FileDialog(title=f"Back up '{db_name}' as…")
        dlg.set_initial_name(default)

        def _chosen(d, result) -> None:
            try:
                target = d.save_finish(result)
                dest = target.get_path() if target else None
            except Exception:
                return
            if not dest:
                return
            self._op_start()

            def _work() -> None:
                from odoo_vite.core import db_backup
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                res = db_backup.backup_database(
                    db_name, dest, db_user=inst.db_user, db_password=pw,
                    instance_id=inst.id, instance_name=inst.name)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        dlg.save(self, None, _chosen)

    def _drop_db_flow(self, instance_id: str, db_name: str) -> None:
        """Standalone drop (B.4): primary row has no Drop button at all."""
        inst = get_instance(instance_id)
        if inst is None:
            return
        if db_name == (inst.primary_db or ""):
            self.toast("The primary database cannot be dropped here — "
                       "switch primary first, or remove the instance")
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text=f"Type '{db_name}' to confirm")
        box.append(entry)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            if entry.get_text().strip() != db_name:
                self.toast("Name did not match — drop cancelled")
                return
            self._op_start()

            def _work() -> None:
                from odoo_vite.core import db_manager
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                # Part A: verify it exists first — never drop blind.
                from odoo_vite.core.db_state import get_db_state

                if not get_db_state(db_name, inst.db_user, pw).exists:
                    GLib.idle_add(self._show_result, instance_id, True,
                                  f"'{db_name}' does not exist — nothing to drop")
                    return
                res = db_manager.drop_database(db_name, inst.db_user, pw)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading=f"Drop database '{db_name}'?",
                body="This permanently deletes the database. The instance "
                     "keeps running on its primary; the name stays tracked "
                     "(untrack it separately if you no longer want it listed).")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Drop permanently")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self._confirm_async(
                f"Drop database '{db_name}'?",
                "Type-to-confirm unavailable here — action cancelled.",
                "Drop", lambda ok: None, destructive=True)

    def _restore_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None or not hasattr(Gtk, "FileDialog"):
            if inst is not None:
                self.toast("File picker unavailable on this GTK version")
            return
        picker = Gtk.FileDialog(title="Select dump file to restore")

        def _file_chosen(d, result) -> None:
            try:
                picked = d.open_finish(result)
                dump = picked.get_path() if picked else None
            except Exception:
                return
            if dump:
                self._restore_dialog(instance_id, inst, dump)

        picker.open(self, None, _file_chosen)

    def _restore_dialog(self, instance_id: str, inst, dump: str) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(
            label="Target database (will be DROPPED and recreated from the "
                  "dump — never merged into live data):", xalign=0, wrap=True))
        entry_target = Gtk.Entry(text=inst.primary_db or "")
        box.append(entry_target)
        entry_confirm = Gtk.Entry(
            placeholder_text="Retype the target name to confirm")
        box.append(entry_confirm)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            target = (entry_target.get_text() or "").strip()
            if not target or entry_confirm.get_text().strip() != target:
                self.toast("Names did not match — restore cancelled")
                return
            self._op_start()

            def _work() -> None:
                from odoo_vite.core import db_backup
                from odoo_vite.core.registry import get_db_password

                try:
                    pw = get_db_password(inst) or None
                except Exception:
                    pw = None
                res = db_backup.restore_database(
                    dump, target, db_user=inst.db_user, db_password=pw)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading="Restore database",
                                  body="Validate-first, drop-and-recreate semantics.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Restore (drop + recreate)")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.toast("Restore dialog unavailable on this GTK version")

    def _validate_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core.db_state import validate_db_config

            try:
                report = validate_db_config(inst)
            except Exception as exc:
                GLib.idle_add(self._show_result, instance_id, False,
                              f"Validation crashed: {exc}")
                return
            GLib.idle_add(self._show_validate_report, instance_id, report)
            GLib.idle_add(self._op_end)

        threading.Thread(target=_work, daemon=True).start()

    def _show_validate_report(self, instance_id: str, report: dict) -> bool:
        checks = (report or {}).get("checks", [])
        failed = [c for c in checks if c.get("ok") is False]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for check in checks:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = "✅" if check.get("ok") is True else (
                "❌" if check.get("ok") is False else "❔")
            row.append(Gtk.Label(label=icon))
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            vbox.append(Gtk.Label(label=str(check.get("field", "?")), xalign=0))
            detail = Gtk.Label(label=str(check.get("detail", "")), xalign=0, wrap=True)
            detail.add_css_class("dim-label")
            vbox.append(detail)
            row.append(vbox)
            box.append(row)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(min(320, 60 + 48 * max(len(checks), 1)))
        scrolled.set_max_content_height(420)
        scrolled.set_child(box)
        summary = ("All checks passed" if not failed
                   else f"{len(failed)} check(s) failing")
        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=f"Config validation: {summary}", body="")
            dlg.add_response("ok", "Close")
            dlg.set_extra_child(scrolled)
            dlg.choose(self, None, lambda d, t: None)
        else:
            self._show_result(instance_id, not failed, summary)
        if failed and self.detail.instance_id == instance_id:
            self.detail.show_error(
                "Config validation: " + "; ".join(
                    f"{c['field']}: {c['detail']}" for c in failed[:3]))
        return False

    # --------------------------------------- Sprint 6 Module Management
    def _mod_instance(self, instance_id: str):
        inst = get_instance(instance_id)
        if inst is None:
            self.toast("Instance disappeared")
            return None, ""
        db_name = (inst.primary_db or "").strip()
        if not db_name:
            self._select_row(instance_id)
            if self.detail.instance_id == instance_id:
                self.detail.show_error("No primary database set — pick one first.")
            return None, ""
        return inst, db_name

    def _load_modules(self, instance_id: str) -> None:
        def _work() -> None:
            from odoo_vite.core import module_manager

            inst = get_instance(instance_id)
            if inst is None:
                return
            db_name = (inst.primary_db or "").strip()
            if not db_name:
                return
            mods = module_manager.list_modules(inst, db_name)
            diff = {}
            if mods.ok:
                dres = module_manager.diff_modules(inst, db_name)
                if dres.ok:
                    diff = {r["name"]: r for r in dres.data.get("diff", [])}
            GLib.idle_add(self._apply_modules, instance_id,
                           list(mods.data.get("modules", [])) if mods.ok else [],
                           diff, "" if mods.ok else mods.message)

        threading.Thread(target=_work, daemon=True).start()

    def _apply_modules(self, instance_id: str, modules: list, diff: dict,
                       error: str) -> bool:
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_modules(modules, diff)
                if error:
                    self.detail.show_error(error)
            except Exception:
                pass
        return False

    def _progress_dialog(self, title: str):
        """Modal log dialog for long module ops. Returns (dialog, append, done)."""
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True,
                         use_header_bar=True, default_width=620, default_height=420)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        close_btn = dlg.get_widget_for_response(Gtk.ResponseType.CLOSE)
        if close_btn is not None:
            close_btn.set_sensitive(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        spin_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Gtk.Spinner(spinning=True)
        spin_row.append(spinner)
        status = Gtk.Label(xalign=0, hexpand=True)
        status.add_css_class("dim-label")
        spin_row.append(status)
        box.append(spin_row)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        view = Gtk.TextView(editable=False, monospace=True)
        scrolled.set_child(view)
        box.append(scrolled)
        dlg.get_content_area().append(box)

        def _append(line: str) -> None:
            buf = view.get_buffer()
            buf.insert(buf.get_end_iter(), line + "\n")
            adj = scrolled.get_vadjustment()
            if adj is not None:
                adj.set_value(adj.get_upper())
            status.set_text(line[-120:])

        def _done(ok: bool, message: str) -> None:
            _append(("Done: " if ok else "FAILED: ") + message)
            spinner.stop()
            if close_btn is not None:
                close_btn.set_sensitive(True)

        dlg.connect("response", lambda *_a: dlg.close())
        return dlg, _append, _done

    def _confirm_command(self, heading: str, command: str, confirm_label: str,
                         callback, extra_child=None) -> None:
        """Confirm dialog showing the exact shell command. callback(bool)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lbl = Gtk.Label(label=command, xalign=0, wrap=True, selectable=True)
        lbl.add_css_class("monospace")
        box.append(lbl)
        if extra_child is not None:
            box.append(extra_child)

        def _on_ok(confirmed: bool) -> None:
            try:
                callback(confirmed)
            except Exception:
                pass

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=heading, body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", confirm_label)
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self._confirm_async(heading, command, confirm_label, _on_ok)

    def _mod_install_flow(self, instance_id: str, names: list) -> None:
        names = [n for n in (names or []) if n]
        if not names:
            self.toast("Select installable modules first")
            return
        inst, db_name = self._mod_instance(instance_id)
        if inst is None:
            return
        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {db_name} -i {','.join(names)} --stop-after-init")

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()
            dlg, append, done = self._progress_dialog(
                f"Install {', '.join(names)}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.install_modules(
                    inst, db_name, names, progress_cb=_feed)
                GLib.idle_add(done, res.ok, res.message)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_modules_only, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        self._confirm_command(f"Install {', '.join(names)} into '{db_name}'?",
                              cmd, "Install", _go)

    def _refresh_modules_only(self, instance_id: str) -> bool:
        self._load_modules(instance_id)
        return False

    def _mod_update_flow(self, instance_id: str, names: list) -> None:
        names = [n for n in (names or []) if n]
        if not names:
            self.toast("Select modules to update first")
            return
        inst, db_name = self._mod_instance(instance_id)
        if inst is None:
            return
        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {db_name} -u {','.join(names)} --stop-after-init")

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()
            dlg, append, done = self._progress_dialog(
                f"Update {', '.join(names)}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                cmd_list = [f"{inst.venv_path}/bin/python",
                            f"{inst.community_path}/odoo-bin",
                            "-c", inst.conf_path, "-d", db_name,
                            "-u", ",".join(names), "--stop-after-init"]
                from odoo_vite.core.proc import run_streaming

                res = run_streaming(cmd_list, progress_cb=_feed, timeout=3600)
                if not res.ok:
                    tail = "\n".join((res.data or {}).get("lines", [])[-10:])
                    msg = f"Update failed: {res.message}" + (
                        f"\n--- tail ---\n{tail}" if tail else "")
                    GLib.idle_add(done, False, msg)
                    GLib.idle_add(self._show_result, instance_id, False, msg)
                else:
                    GLib.idle_add(done, True, f"Updated {', '.join(names)}")
                    GLib.idle_add(self._show_result, instance_id, True,
                                  f"Updated {', '.join(names)}")
                GLib.idle_add(self._refresh_modules_only, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        self._confirm_command(f"Update {', '.join(names)} in '{db_name}'?",
                              cmd, "Update", _go)

    def _mod_update_code_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(
            label="This pulls code (community git), reinstalls Python deps, "
                  "then updates modules. The instance must stay stopped; "
                  "restart it yourself afterwards.", xalign=0, wrap=True))
        backup_check = Gtk.CheckButton(
            label="Back up the primary database first (recommended)",
            active=True)
        box.append(backup_check)
        mods_lbl = Gtk.Label(xalign=0)
        mods_lbl.add_css_class("dim-label")
        box.append(mods_lbl)
        try:
            mods_lbl.set_text(
                "Modules: " + ", ".join(inst.auto_update_modules or [])
                + " (from auto-update list)" if inst.auto_update_modules
                else "Modules: all installed upgradeable modules")
        except Exception:
            pass

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            mods = list(inst.auto_update_modules or [])
            if not mods:
                self.toast("No auto-update modules configured — nothing to update")
                return
            self._op_start()
            if backup_check.get_active():
                self._backup_before_update(instance_id, inst, mods)
            else:
                self._run_update_code(instance_id, inst, mods)

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading="Update code and modules?",
                body="Riskier than Install: this changes code on disk, not "
                     "just database state.")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Update code")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _go(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self._confirm_async("Update code and modules?",
                                "Code on disk will change.", "Update code", _go,
                                destructive=True)

    def _backup_before_update(self, instance_id: str, inst, mods: list) -> None:
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = str(Path(inst.path) / f"pre-update-{inst.primary_db}-{stamp}.dump")

        def _work() -> None:
            from odoo_vite.core import db_backup
            from odoo_vite.core.registry import get_db_password

            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            res = db_backup.backup_database(
                inst.primary_db, dest, db_user=inst.db_user, db_password=pw,
                instance_id=inst.id, instance_name=inst.name)
            if not res.ok:
                GLib.idle_add(self._show_result, instance_id, False,
                              f"Pre-update backup failed, aborting update: {res.message}")
                GLib.idle_add(self._op_end)
                return
            GLib.idle_add(self.toast, f"Pre-update backup saved: {dest}")
            self._run_update_code(instance_id, inst, mods)

        threading.Thread(target=_work, daemon=True).start()

    def _run_update_code(self, instance_id: str, inst, mods: list) -> None:
        dlg, append, done = self._progress_dialog("Update code + modules")
        dlg.present()

        def _feed(line: str) -> None:
            GLib.idle_add(append, line)

        def _work() -> None:
            from odoo_vite.core import module_manager

            res = module_manager.update_code(inst, mods, progress_cb=_feed)
            GLib.idle_add(done, res.ok, res.message)
            GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self._refresh_modules_only, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _mod_uninstall_flow(self, instance_id: str, name: str) -> None:
        if not name:
            return
        inst, db_name = self._mod_instance(instance_id)
        if inst is None:
            return

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()
            dlg, append, done = self._progress_dialog(f"Uninstall {name}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.uninstall_modules(
                    inst, db_name, [name], progress_cb=_feed)
                GLib.idle_add(done, res.ok, res.message)
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_modules_only, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        self._confirm_async(
            f"Uninstall '{name}'?",
            f"Uninstalling can cascade to dependent modules. Odoo itself "
            f"will refuse or report blocking dependents in '{db_name}' — "
            "whatever it reports is shown verbatim.",
            "Uninstall", _go, destructive=True)

    def _mod_deps_dialog(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import module_manager

            db_name = (inst.primary_db or "").strip()
            res = module_manager.get_dependency_graph(inst, db_name) if db_name else None
            GLib.idle_add(self._show_deps_dialog, instance_id,
                           res.data if res and res.ok else None,
                           "" if res and res.ok else (res.message if res else "no DB"))
            GLib.idle_add(self._op_end)

        threading.Thread(target=_work, daemon=True).start()

    def _show_deps_dialog(self, instance_id: str, graph: dict | None,
                          error: str) -> bool:
        if graph is None:
            self.toast(f"Dependency graph unavailable: {error[:160]}")
            return False
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        depends: dict[str, list] = {}
        required_by: dict[str, list] = {}
        for src, dst in edges:
            depends.setdefault(src, []).append(dst)
            required_by.setdefault(dst, []).append(src)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search = Gtk.SearchEntry(placeholder_text="Module name…", text="")
        outer.append(search)
        lists = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                        hexpand=True, vexpand=True)
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left_box.append(Gtk.Label(label="Depends on", xalign=0))
        left_list = Gtk.ListBox()
        left_box.append(left_list)
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right_box.append(Gtk.Label(label="Required by", xalign=0))
        right_list = Gtk.ListBox()
        right_box.append(right_list)
        lists.append(left_box)
        lists.append(right_box)
        outer.append(lists)

        def _fill(mod_name: str) -> None:
            for lb, data in ((left_list, depends.get(mod_name, [])),
                             (right_list, required_by.get(mod_name, []))):
                while True:
                    row = lb.get_row_at_index(0)
                    if row is None:
                        break
                    lb.remove(row)
                for dep in sorted(data):
                    lb.append(Gtk.Label(label=dep, xalign=0))

        search.connect("search-changed",
                       lambda _e: _fill(search.get_text().strip()))
        if nodes:
            _fill(nodes[0]["name"])
            search.set_text(nodes[0]["name"])

        webview = self._try_dependency_webview(graph)
        if webview is not None:
            webview.set_size_request(-1, 300)
            outer.append(webview)
        else:
            note = Gtk.Label(xalign=0, wrap=True)
            note.add_css_class("dim-label")
            note.set_text("Interactive graph unavailable here (no WebKit) — "
                          "lists above carry the same data.")
            outer.append(note)

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(
                heading=f"Dependencies ({len(nodes)} modules, {len(edges)} edges)",
                body="")
            dlg.add_response("ok", "Close")
            dlg.set_extra_child(outer)
            dlg.set_size_request(680, 560)
            dlg.choose(self, None, lambda d, t: None)
        else:
            self.toast(f"{len(nodes)} modules, {len(edges)} edges (no dialog backend)")
        return False

    def _try_dependency_webview(self, graph: dict):
        """d3 force graph in WebKit if available, else None (native fallback)."""
        try:
            import gi as _gi

            _gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit as _WebKit
        except Exception:
            return None
        try:
            import json as _json

            data = _json.dumps({"nodes": [{"id": n["name"]} for n in graph.get("nodes", [])],
                                "links": [{"source": s, "target": d}
                                          for s, d in graph.get("edges", [])]})
            html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>body{margin:0;background:#242424;color:#eee;font:12px sans-serif}</style>
</head><body><div id="msg"></div><svg width="640" height="280"></svg>
<script>
try {
const DATA = __DATA__;
const svg = d3.select("svg"), w = 640, h = 280;
const sim = d3.forceSimulation(DATA.nodes).force("link", d3.forceLink(DATA.links).id(d=>d.id).distance(60)).force("charge", d3.forceManyBody().strength(-120)).force("center", d3.forceCenter(w/2,h/2));
const link = svg.append("g").attr("stroke","#888").selectAll("line").data(DATA.links).join("line");
const node = svg.append("g").selectAll("circle").data(DATA.nodes).join("circle").attr("r",6).attr("fill","#3584e4").call(d3.drag().on("start",(e,d)=>{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=e.x;d.fy=e.y;}).on("drag",(e,d)=>{d.fx=e.x;d.fy=e.y;}).on("end",(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}));
node.append("title").text(d=>d.id);
sim.on("tick",()=>{link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);node.attr("cx",d=>d.x).attr("cy",d=>d.y);});
} catch(e){document.getElementById("msg").textContent="d3 failed to load (offline?) — use the lists above.";}
</script></body></html>""".replace("__DATA__", data)
            view = _WebKit.WebView()
            view.load_html(html, "file:///")
            return view
        except Exception:
            return None

    def _mod_scaffold_wizard(self, instance_id: str) -> None:
        from odoo_vite.ui.wizard_scaffold import ScaffoldWizard

        inst = get_instance(instance_id)
        if inst is None:
            return
        wiz = ScaffoldWizard(parent=self, custom_addons=inst.custom_addons_path or "",
                             odoo_version=inst.version or "17.0")
        wiz.present()

    # --------------------------------------- Sprint 7 Configuration
    def _conf_save_flow(self, instance_id: str, changes: dict) -> None:
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self._show_result, instance_id, False,
                              "Instance disappeared")
                return
            res = conf_manager.update_conf_keys(inst.conf_path, changes or {})
            GLib.idle_add(self._show_conf_saved, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _show_conf_saved(self, instance_id: str, ok: bool, message: str) -> bool:
        self._show_result(instance_id, ok, message)
        if ok:
            try:
                if self.detail.instance_id == instance_id:
                    self.detail.refresh_conf()
                    if self.detail._running:
                        self.detail.lbl_conf_notice.set_text(
                            "Saved — takes effect on next restart "
                            "(instance is running).")
                    else:
                        self.detail.lbl_conf_notice.set_text("")
            except Exception:
                pass
        return False

    def _conf_restore_flow(self, instance_id: str) -> None:
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self._show_result, instance_id, False,
                              "Instance disappeared")
                return
            res = conf_manager.restore_conf_backup(inst.conf_path)
            GLib.idle_add(self._show_conf_saved, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    def _conf_regenerate_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._select_row(instance_id)

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()

            def _work() -> None:
                from odoo_vite.core import conf_manager

                res = conf_manager.regenerate_conf(inst)
                GLib.idle_add(self._show_conf_saved, instance_id, res.ok,
                              res.message)

            threading.Thread(target=_work, daemon=True).start()

        self._confirm_async(
            "Regenerate odoo.conf from registry?",
            "This OVERWRITES manual key edits with a fresh [options] built "
            "purely from registry fields (port, db user, addons paths). "
            "Unknown sections are preserved; a backup is taken first. "
            "Manual edits not reflected in the registry WILL be lost. Proceed?",
            "Regenerate", _go, destructive=True)

    def _meta_save_flow(self, instance_id: str, meta: dict) -> None:
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import conf_manager

            inst = get_instance(instance_id)
            if inst is None:
                GLib.idle_add(self._show_result, instance_id, False,
                              "Instance disappeared")
                return
            res = update_instance(
                instance_id,
                description=meta.get("description", ""),
                workers=int(meta.get("workers", 0) or 0),
                log_level=meta.get("log_level", "info"),
                python_binary=meta.get("python_binary", ""))
            if not res.ok:
                GLib.idle_add(self._show_result, instance_id, False, res.message)
                return
            # workers/log_level are real conf keys: same validated path.
            conf_res = conf_manager.update_conf_keys(inst.conf_path, {
                "workers": str(int(meta.get("workers", 0) or 0)),
                "log_level": meta.get("log_level", "info"),
            })
            if not conf_res.ok:
                GLib.idle_add(self._show_result, instance_id, False,
                              f"Metadata saved, but conf write failed: "
                              f"{conf_res.message}")
                return
            GLib.idle_add(self._show_result, instance_id, True,
                          "Metadata saved (registry + odoo.conf)")
            GLib.idle_add(self._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()

    def _addons_manage_dialog(self, instance_id: str) -> None:
        from odoo_vite.core import addon_paths

        inst = get_instance(instance_id)
        if inst is None:
            return
        self._select_row(instance_id)
        try:
            entries = addon_paths.get_addons_state(inst)
        except Exception as exc:
            self.toast(f"Cannot load addon paths: {exc}")
            return

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(Gtk.Label(
            label="Order matters (first match wins in Odoo). Unchecked paths "
                  "stay listed but are excluded from addons_path. Every Apply "
                  "rewrites the conf through the validated path.",
            xalign=0, wrap=True))
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(220)
        scrolled.set_max_content_height(360)
        outer.append(scrolled)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.set_child(listbox)

        rows: list[dict] = []

        def _rebuild() -> None:
            # Sync current toggle states back first, or a move/remove would
            # silently drop them (rows are rebuilt from `entries`).
            for existing in rows:
                try:
                    existing["entry"]["enabled"] = bool(
                        existing["check"].get_active())
                except Exception:
                    pass
            while True:
                row = listbox.get_row_at_index(0) if hasattr(
                    listbox, "get_row_at_index") else None
                if row is None:
                    break
                listbox.remove(row)
            for child in list(listbox):
                listbox.remove(child)
            rows.clear()
            for entry in entries:
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                check = Gtk.CheckButton(active=bool(entry.get("enabled", True)))
                bind_check_highlight(check)
                hbox.append(check)
                lbl = Gtk.Label(label=entry.get("path", ""), xalign=0, hexpand=True,
                                wrap=True)
                lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                lbl.set_max_width_chars(46)
                hbox.append(lbl)
                btn_edit = Gtk.Button(label="Edit")
                btn_edit.set_tooltip_text(
                    "Change this path string (e.g. folder renamed/moved)")
                btn_edit.connect("clicked", self._addons_edit, entries, entry,
                                 _rebuild)
                hbox.append(btn_edit)
                btn_up = Gtk.Button(label="↑")
                btn_up.connect("clicked", self._addons_move, entries, entry,
                               -1, _rebuild)
                hbox.append(btn_up)
                btn_down = Gtk.Button(label="↓")
                btn_down.connect("clicked", self._addons_move, entries, entry,
                                 1, _rebuild)
                hbox.append(btn_down)
                btn_rm = Gtk.Button(label="Remove")
                btn_rm.connect("clicked", self._addons_remove, entries, entry,
                               _rebuild)
                hbox.append(btn_rm)
                listbox.append(hbox)
                rows.append({"entry": entry, "check": check, "box": hbox})

        def _collect() -> list:
            return [{"path": r["entry"]["path"],
                     "enabled": bool(r["check"].get_active())} for r in rows]

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_row.append(Gtk.Label(label="Add folder:"))
        path_lbl = Gtk.Label(xalign=0, hexpand=True)
        path_lbl.add_css_class("dim-label")
        path_lbl.set_text("—")
        add_row.append(path_lbl)
        picked: dict = {}

        def _browse(_btn) -> None:
            if not hasattr(Gtk, "FileDialog"):
                self.toast("Folder picker unavailable")
                return
            dlg = Gtk.FileDialog(title="Select addons folder")
            dlg.select_folder(
                self, None,
                lambda d, t: _folder_picked(d, t, picked, path_lbl))

        def _folder_picked(dlg, result, picked, path_lbl) -> None:
            try:
                folder = dlg.select_folder_finish(result)
                path = folder.get_path() if folder else None
            except Exception:
                return
            if not path:
                return
            picked["path"] = path
            path_lbl.set_text(path)
            if not addon_paths.looks_like_addons_folder(path):
                self.toast("Warning: no subfolder with __manifest__.py found — "
                           "you may still add it")

        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", _browse)
        add_row.append(btn_browse)

        def _add(_btn) -> None:
            path = (picked.get("path") or "").strip()
            if not path:
                self.toast("Browse for a folder first")
                return
            if path in [e["path"] for e in entries]:
                self.toast("Already in the list")
                return
            entries.append({"path": path, "enabled": True})
            picked.clear()
            path_lbl.set_text("—")
            _rebuild()

        btn_add = Gtk.Button(label="Add")
        btn_add.connect("clicked", _add)
        add_row.append(btn_add)
        outer.append(add_row)
        _rebuild()

        def _apply(confirmed: bool) -> None:
            if not confirmed:
                return
            self._op_start()

            def _work() -> None:
                res = addon_paths.apply_addons_state(instance_id, _collect())
                GLib.idle_add(self._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_detail_dbs, instance_id)
                if res.ok:
                    GLib.idle_add(self._refresh_conf_tab, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading=f"Addon paths — {inst.name}", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Apply changes")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(outer)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            dlg.choose(self, None,
                       lambda d, t: _apply(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.toast("Addon manager needs libadwaita dialogs")

    def _addons_edit(self, _btn, entries: list, entry: dict,
                     rebuild=None) -> None:
        """9.3 Edit: change an entry's path string in place (rename/move)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(label="New path for this entry:", xalign=0))
        field = Gtk.Entry(text=entry.get("path", ""))
        box.append(field)

        def _on_ok(confirmed: bool) -> None:
            if not confirmed:
                return
            new_path = (field.get_text() or "").strip()
            # NOTE: AlertDialog closes on response, so validation failures
            # toast (they couldn't stay visible in a closed dialog).
            if not new_path:
                self.toast("Path must not be empty — edit cancelled")
                return
            if new_path != entry.get("path") and new_path in [
                    e.get("path") for e in entries]:
                self.toast("That path is already in the list — edit cancelled")
                return
            entry["path"] = new_path
            from odoo_vite.core import addon_paths

            if not addon_paths.looks_like_addons_folder(new_path):
                self.toast("Saved — note: no subfolder with __manifest__.py "
                           "found there")
            if rebuild is not None:
                try:
                    rebuild()
                except Exception:
                    pass
            try:
                _dlg_holder["dlg"].close()
            except Exception:
                pass

        _dlg_holder: dict = {}
        if HAS_ADW and HAS_ALERT:
            dlg = Adw.AlertDialog(heading="Edit addon path", body="")
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Save")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_extra_child(box)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")
            _dlg_holder["dlg"] = dlg
            dlg.choose(self, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.toast("Edit dialog unavailable on this GTK version")

    def _addons_move(self, _btn, entries: list, entry: dict, delta: int,
                     rebuild=None) -> None:
        try:
            idx = entries.index(entry)
        except ValueError:
            return
        other = idx + delta
        if 0 <= other < len(entries):
            entries[idx], entries[other] = entries[other], entries[idx]
            if rebuild is not None:
                try:
                    rebuild()
                except Exception:
                    pass

    def _addons_remove(self, _btn, entries: list, entry: dict,
                       rebuild=None) -> None:
        try:
            entries.remove(entry)
        except ValueError:
            pass
        if rebuild is not None:
            try:
                rebuild()
            except Exception:
                pass

    def _refresh_conf_tab(self, instance_id: str) -> bool:
        if self.detail.instance_id == instance_id:
            try:
                self.detail.refresh_conf()
            except Exception:
                pass
        return False

    # --------------------------------------- Sprint 8 Monitoring flows
    def _log_search_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        pattern = ""
        level = None
        try:
            pattern = self.detail.entry_log_search.get_text() or ""
            item = self.detail.drop_log_level.get_selected_item()
            text = item.get_string() if item is not None else "All levels"
            level = None if text == "All levels" else text
        except Exception:
            pass
        if not pattern.strip():
            self.toast("Enter a regex pattern to search")
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import log_search

            res = log_search.search_file(inst.log_path or "", pattern,
                                         level=level, context=2)
            GLib.idle_add(self._show_search_results, instance_id,
                           res.ok, res.message,
                           (res.data or {}).get("matches", []) if res.ok else [])
            GLib.idle_add(self._op_end)

        threading.Thread(target=_work, daemon=True).start()

    def _show_search_results(self, instance_id: str, ok: bool, message: str,
                             matches: list) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                if ok:
                    self.detail.set_search_results(matches, message)
                else:
                    self.detail.set_search_results([], message)
            except Exception:
                pass
        return False

    def _log_doctor_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None or not inst.log_path:
            self.toast("No log file recorded for this instance")
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import log_doctor

            try:
                findings = log_doctor.diagnose_file(inst.log_path)
            except Exception as exc:
                GLib.idle_add(self._show_result, instance_id, False,
                              f"Doctor failed: {exc}")
                return
            GLib.idle_add(self._show_doctor_findings, instance_id, findings)
            GLib.idle_add(self._op_end)

        threading.Thread(target=_work, daemon=True).start()

    def _show_doctor_findings(self, instance_id: str, findings: list) -> bool:
        self._op_end()
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_doctor_findings(findings)
                if not findings:
                    self.toast("Doctor found no known issues in the log")
            except Exception:
                pass
        return False

    def _slow_refresh_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import db_manager
            from odoo_vite.core.registry import get_db_password

            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            if not db_manager.pg_stat_statements_enabled(inst.db_user, pw):
                GLib.idle_add(self._show_slow, instance_id, False,
                              "pg_stat_statements is not available to this role. "
                              "Ask your Postgres admin to run: CREATE EXTENSION "
                              "pg_stat_statements; (first enable needs "
                              "shared_preload_libraries + a server restart — "
                              "Odoo Vite won't do that for you: it restarts "
                              "Postgres for every instance on the box.)", [])
                return
            res = db_manager.slow_queries(inst.primary_db, inst.db_user, pw)
            GLib.idle_add(self._show_slow, instance_id, res.ok, res.message,
                           (res.data or {}).get("queries", []) if res.ok else [])

        threading.Thread(target=_work, daemon=True).start()

    def _show_slow(self, instance_id: str, ok: bool, message: str,
                   rows: list) -> bool:
        # _op_end accounting: _show_result also ends; call exactly one path.
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_slow_queries(ok, message, rows)
            except Exception:
                pass
        self._op_end()
        return False

    def _profile_flow(self, instance_id: str, duration: int = 10) -> None:
        from odoo_vite.core import profiler
        from odoo_vite.core.process_manager import _alive_pid

        inst = get_instance(instance_id)
        if inst is None:
            return
        pid = _alive_pid(inst)
        if pid is None:
            self._select_row(instance_id)
            if self.detail.instance_id == instance_id:
                self.detail.show_error(
                    "Profile needs a running process — start the instance first.")
            return
        if profiler.py_spy_path() is None:
            self._confirm_async(
                "py-spy is not installed",
                "Flame graphs need py-spy (pip package, user-scoped install, "
                "no sudo required). Install it now with 'pip install py-spy'?",
                "Install py-spy",
                lambda ok: self._install_pyspy_then_profile(instance_id, pid,
                                                            duration)
                if ok else None)
            return
        self._run_profile(instance_id, pid, duration)

    def _install_pyspy_then_profile(self, instance_id: str, pid: int,
                                    duration: int) -> None:
        self._op_start()

        def _work() -> None:
            from odoo_vite.core import profiler

            res = profiler.ensure_py_spy()
            if not res.ok:
                GLib.idle_add(self._show_result, instance_id, False, res.message)
                return
            # Op stays held: _run_profile takes it over (balanced by its end).
            GLib.idle_add(self._run_profile, instance_id, pid, duration, True)

        threading.Thread(target=_work, daemon=True).start()

    def _run_profile(self, instance_id: str, pid: int, duration: int,
                     _held: bool = False) -> None:
        import os
        import tempfile

        if not _held:
            self._op_start()
        dest = os.path.join(
            tempfile.gettempdir(), f"odoo-vite-profile-{instance_id[:8]}.svg")

        def _work() -> None:
            from odoo_vite.core import profiler

            res = profiler.profile_pid(pid, duration=duration, output_svg=dest)
            GLib.idle_add(self._show_profile_result, instance_id, res.ok,
                           res.message,
                           (res.data or {}).get("svg", "") if res.ok else "")

        threading.Thread(target=_work, daemon=True).start()

    def _show_profile_result(self, instance_id: str, ok: bool, message: str,
                             svg: str) -> bool:
        self._op_end()
        if not ok:
            self._select_row(instance_id)
            if self.detail.instance_id == instance_id:
                self.detail.show_error(message)
            return False
        self.toast(message)
        self._view_svg(svg)
        return False

    def _view_svg(self, path: str) -> None:
        shown = False
        try:
            from gi.repository import Gio as _Gio

            pic = Gtk.Picture.new_for_filename(path)
            pic.set_can_shrink(True)
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_min_content_height(420)
            scrolled.set_child(pic)
            if HAS_ADW and HAS_ALERT:
                dlg = Adw.AlertDialog(heading="Flame graph", body="")
                dlg.add_response("ok", "Close")
                dlg.set_extra_child(scrolled)
                dlg.set_size_request(720, 520)
                dlg.choose(self, None, lambda d, t: None)
                shown = True
        except Exception:
            shown = False
        if not shown:
            try:
                from gi.repository import Gio as _Gio2

                _Gio2.AppInfo.launch_default_for_uri(
                    Path(path).as_uri(), None)
                self.toast(f"Opened {path} externally")
            except Exception:
                self.toast(f"Profile saved: {path}")

    # ----------------------------------- Sprint 10 Dev Tools flows
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
            changed = {k: (current.get(k), v) for k, v in values.items()
                       if str(current.get(k)) != str(v)}
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
        self._confirm_command(
            f"Run {module} tests on '{target}'?",
            f"{cmd}\n\nTarget is disposable (never the primary). Missing DBs "
            "are created+installed (-i); existing ones are updated+tested (-u).",
            "Run tests", _go)

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
            bind_check_highlight(check)
            db_checks[db_name] = check
            box.append(check)
        if inst.primary_db and inst.primary_db not in db_checks:
            check = Gtk.CheckButton(label=f"{inst.primary_db}  (primary)")
            check.set_active(False)
            bind_check_highlight(check)
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
            # Part B: live per-DB state (bg fetch — never on the main loop).
            threading.Thread(target=self._load_db_states,
                             args=(instance_id,), daemon=True).start()
        return False

    def _load_db_states(self, instance_id: str) -> None:
        from odoo_vite.core.db_state import get_db_state, human_size, odoo_major
        from odoo_vite.core.registry import get_db_password

        try:
            inst = get_instance(instance_id)
            if inst is None:
                return
            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            names = list(dict.fromkeys(
                (inst.tracked_dbs or []) + ([inst.primary_db] if inst.primary_db else [])))
            states: dict = {}
            for name in names:
                try:
                    st = get_db_state(name, inst.db_user, pw)
                except Exception:
                    continue
                states[name] = {
                    "exists": st.exists,
                    "initialized": st.initialized,
                    "odoo_version": st.odoo_version or "",
                    "odoo_major": odoo_major(st.odoo_version),
                    "size": human_size(st.size_bytes),
                    "owner": st.owner or "",
                }
            GLib.idle_add(self._apply_db_states, instance_id,
                           states, inst.version or "")
        except Exception:
            pass

    def _apply_db_states(self, instance_id: str, states: dict,
                         version: str) -> bool:
        if self.detail.instance_id == instance_id:
            try:
                self.detail.set_db_states(states, version)
            except Exception:
                pass
        return False
