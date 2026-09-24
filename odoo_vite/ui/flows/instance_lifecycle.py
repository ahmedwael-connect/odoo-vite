"""Instance lifecycle flows: start/stop/restart/repair, first-start
confirm + collision, primary/switch/track/untrack/discover, remove. (Sprint R decomposition — moved verbatim from MainWindow,
behavior unchanged; `self.win` is the MainWindow for toasts, dialogs,
navigation, polling and detail/sidebar access)."""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core import process_manager  # noqa: E402
from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui import HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import (  # noqa: E402
    _ask_blocking,
    _finish_alert,
    bind_check_highlight,
    filter_checks,
)


class InstanceLifecycleFlows:
    """See module docstring. Constructed once: InstanceLifecycleFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _run_simple_flow(self, action: str, instance_id: str) -> None:
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            if action == "stop":
                res = process_manager.stop_instance(instance_id)
            else:
                res = process_manager.restart_instance(instance_id)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def start_flow(self, instance_id: str, database: str | None = None,
                   _op_held: bool = False) -> None:
        """Start flow with first-start confirm + collision handling (bg thread)."""
        if not _op_held:
            self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            res = process_manager.start_instance(
                instance_id, database=database, confirm_cb=self._confirm_cb)
            if not res.ok and (res.data or {}).get("collision"):
                # Op stays held across the recursive retry (balanced by its end).
                self._handle_collision(instance_id, res.data)
                return
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _repair_flow(self, instance_id: str) -> None:
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            from odoo_vite.core import venv_manager

            res = venv_manager.repair_venv(instance_id)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


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
                GLib.idle_add(self.win._show_result, instance_id, False,
                              "Aborted — no valid new database name given")
        else:
            GLib.idle_add(self.win._show_result, instance_id, False,
                          "Start aborted by user (database collision)")

    # ------------------------------------------- async confirm helper (UI flows)


    def _set_primary_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import is_valid_identifier

        self.win._op_start()

        if not db_name or not is_valid_identifier(db_name):
            self.win._select_row(instance_id)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error(
                    "Pick a valid database name first (or choose Other… and type one).")
            self.win._op_end()
            return

        def _work() -> None:
            res = update_instance(instance_id, primary_db=db_name)
            GLib.idle_add(self.win._show_result, instance_id, res.ok,
                          f"Primary database set to '{db_name}'" if res.ok
                          else res.message)
            GLib.idle_add(self.win._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _switch_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import is_valid_identifier

        if not db_name or not is_valid_identifier(db_name):
            self.win._select_row(instance_id)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error("Pick a valid database name to switch to.")
            return
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            res = process_manager.switch_database(
                instance_id, db_name, confirm_cb=self._confirm_cb)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self.win._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _track_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import track_database

        self.win._op_start()

        def _work() -> None:
            res = track_database(instance_id, db_name)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self.win._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _untrack_flow(self, instance_id: str, db_name: str) -> None:
        from odoo_vite.core.db_manager import untrack_database

        inst = get_instance(instance_id)
        if inst is not None and db_name == (inst.primary_db or ""):
            self.win._confirm_async(
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

        self.win._op_start()

        def _work() -> None:
            res = untrack_database(instance_id, db_name)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
            GLib.idle_add(self.win._refresh_detail_dbs, instance_id)

        threading.Thread(target=_work, daemon=True).start()


    def _discover_flow(self, instance_id: str) -> None:
        from odoo_vite.core import db_manager

        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()
        self.win._select_row(instance_id)

        def _work() -> None:
            from odoo_vite.core.db_state import get_db_state, odoo_major
            from odoo_vite.core.registry import get_db_password

            try:
                pw = get_db_password(inst) or None
            except Exception:
                pw = None
            res = db_manager.list_databases_for_user(inst.db_user, pw)
            if not res.ok:
                GLib.idle_add(self.win._show_result, instance_id, False, res.message)
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
            self.win._op_end()
            return False
        groups = group_discover(entries, instance_version)
        total = sum(len(v) for v in groups.values())
        if total == 0:
            self.win.toast("No new databases — everything found is tracked")
            self.win._op_end()
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
                self.win._op_end()
                return

            def _work() -> None:
                picked = [n for n, c in checks.items() if c.get_active()]
                for name in picked:
                    track_database(instance_id, name)
                GLib.idle_add(self.win._show_result, instance_id, True,
                              f"Tracked {len(picked)} database(s)" if picked
                              else "Nothing selected")
                GLib.idle_add(self.win._refresh_detail_dbs, instance_id)

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
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            # Fallback: track everything found, no per-item choice.
            _on_ok(True)
        return False

    # --------------------------------------- Sprint 5 Database Operations


    def _remove_flow(self, instance_id: str) -> None:
        from odoo_vite.core import removal

        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._select_row(instance_id)
        if (inst.mode or "managed").lower() == "adopted":
            self.win._confirm_async(
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
                self.win.toast("Name did not match — removal cancelled")
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
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win._confirm_async(
                f"Remove '{inst.name}'?",
                f"Type-to-confirm unavailable here — press Remove to delete "
                f"{inst.path}. No database is dropped in this fallback.",
                "Remove", lambda ok: self._do_remove(instance_id, False)
                if ok else None, destructive=True)


    def _do_remove(self, instance_id: str, drop_db: bool,
                   drop_extra_dbs: list | None = None) -> None:
        from odoo_vite.core import removal

        self.win._op_start()

        def _work() -> None:
            res = removal.remove_instance(instance_id, drop_db=drop_db,
                                          drop_extra_dbs=drop_extra_dbs)
            GLib.idle_add(self._after_remove, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _after_remove(self, instance_id: str, ok: bool, message: str) -> bool:
        self.win._op_end()
        if ok:
            self.win.toast(message)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_instance(None)
        else:
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error(message)
            else:
                self.win.toast(message)
        self.win.sidebar.full_refresh()
        self.win._poll_tick()
        return False


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


