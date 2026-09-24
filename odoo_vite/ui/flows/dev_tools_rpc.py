"""Dev Tools RPC flows: connect, model inspector, record browser, cron, launch.json, editors. (Sprint R — moved verbatim from MainWindow; `self.win` is the MainWindow)."""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance, update_instance  # noqa: E402
from odoo_vite.ui import Adw, HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import _finish_alert  # noqa: E402
from odoo_vite.ui.flows.module_ops import diff_record_values  # noqa: E402



class DevToolsRpcFlows:
    """Dev Tools RPC flows: connect, model inspector, record browser, cron, launch.json, editors. Constructed once: DevToolsRpcFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _rpc_session(self, instance_id: str):
        return (self.win._rpc_sessions or {}).get(instance_id)


    def _rpc_connect_flow(self, instance_id: str) -> None:
        from odoo_vite.core import odoo_rpc

        inst = get_instance(instance_id)
        if inst is None:
            return
        try:
            user, password, remember = self.win.detail.rpc_credentials()
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
                    self.win.detail.entry_rpc_user.set_text(user)
                    self.win.detail.entry_rpc_pass.set_text(password)
                except Exception:
                    pass
        try:
            user, password, remember = self.win.detail.rpc_credentials()
        except Exception:
            user, password, remember = "", "", False
        self.win._select_row(instance_id)
        self.win._op_start()

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
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_rpc_status(message)
            except Exception:
                pass
        if ok:
            if self.win._rpc_sessions is None:
                self.win._rpc_sessions = {}
            self.win._rpc_sessions[instance_id] = client
            self.win.toast(message)
            self._dev_list_models(instance_id)
        return False


    def _dev_client(self, instance_id: str):
        client = (self.win._rpc_sessions or {}).get(instance_id)
        if client is None and self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_rpc_status(
                    "Not connected — enter Odoo credentials and Connect first.")
            except Exception:
                pass
        return client


    def _dev_list_models(self, instance_id: str) -> None:
        client = self._rpc_session(instance_id)
        if client is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.list_models(client)
            GLib.idle_add(self._show_models, instance_id, res.ok,
                           res.data.get("models", []) if res.ok else [],
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _show_models(self, instance_id: str, ok: bool, models: list,
                     error: str) -> bool:
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                if ok:
                    self.win.detail.render_model_rows(models)
                else:
                    self.win.detail.set_rpc_status(error)
            except Exception:
                pass
        return False


    def _dev_model_selected(self, instance_id: str, model: str) -> None:
        if not model:
            return
        client = self._dev_client(instance_id)
        if client is None:
            return
        self.win._op_start()

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
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                if ok:
                    self.win.detail.set_model_metadata(meta)
                else:
                    self.win.detail.set_rpc_status(error)
            except Exception:
                pass
        return False

    # --------------------------------------------------- records


    def _rec_domain(self):
        try:
            field = (self.win.detail.entry_dom_field.get_text() or "").strip()
            item = self.win.detail.drop_dom_op.get_selected_item()
            op = item.get_string() if item is not None else "="
            value = (self.win.detail.entry_dom_value.get_text() or "").strip()
        except Exception:
            return []
        if not field:
            return []
        return [[field, op, value]]


    def _dev_records_page(self, instance_id: str, model: str, offset: int) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        self.win._op_start()

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
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                if ok:
                    self.win.detail.set_records(records, offset, 50, model)
                else:
                    self.win.detail.set_rpc_status(error)
            except Exception:
                pass
        return False


    def _rec_search_flow(self, instance_id: str) -> None:
        model = ""
        try:
            model = self.win.detail.selected_model() or self.win.detail._records_model
        except Exception:
            pass
        if not model:
            self.win.toast("Pick a model in the inspector first")
            return
        self._dev_records_page(instance_id, model, 0)


    def _rec_page_flow(self, instance_id: str, delta: int) -> None:
        try:
            model = self.win.detail._records_model
            offset = max(0, self.win.detail._records_offset + delta * 50)
        except Exception:
            return
        if not model:
            return
        self._dev_records_page(instance_id, model, offset)


    def _rec_new_flow(self, instance_id: str) -> None:
        try:
            model = self.win.detail.selected_model() or self.win.detail._records_model
        except Exception:
            model = ""
        if not model:
            self.win.toast("Pick a model in the inspector first")
            return
        self._record_edit_dialog(instance_id, model, None, {})


    def _rec_edit_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        try:
            model = self.win.detail._records_model
            record_id = self.win.detail.selected_record()
        except Exception:
            return
        if not model or not record_id:
            self.win.toast("Select a record first")
            return
        self.win._op_start()

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
        self.win._op_end()
        if not ok:
            self.win.toast(f"Cannot read record: {error[:160]}")
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
                self.win.toast("No field values given")
                return
            if is_new:
                self.win._op_start()

                def _work() -> None:
                    from odoo_vite.core import odoo_inspect

                    client = self._dev_client(instance_id)
                    if client is None:
                        GLib.idle_add(self.win._op_end)
                        return
                    res = odoo_inspect.create_record(client, model, values)
                    GLib.idle_add(self.win._show_result, instance_id, res.ok,
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
                self.win.toast("No changes vs current values")
                return
            preview = "\n".join(f"{k}: {old!r} → {new!r}"
                                for k, (old, new) in changed.items())
            only = {k: new for k, (old, new) in changed.items()}

            def _commit(ok2: bool) -> None:
                if not ok2:
                    return
                self.win._op_start()

                def _work2() -> None:
                    from odoo_vite.core import odoo_inspect

                    client = self._dev_client(instance_id)
                    if client is None:
                        GLib.idle_add(self.win._op_end)
                        return
                    res = odoo_inspect.update_record(client, model, record_id,
                                                     only)
                    GLib.idle_add(self.win._show_result, instance_id, res.ok,
                                   res.message)
                    if res.ok:
                        try:
                            off = self.win.detail._records_offset
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
                dlg2.choose(self.win, None,
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
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win.toast("Record editor needs libadwaita dialogs")


    def _rec_delete_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        try:
            model = self.win.detail._records_model
            record_id = self.win.detail.selected_record()
        except Exception:
            return
        if not model or not record_id:
            self.win.toast("Select a record first")
            return
        label = ""
        try:
            for rec in self.win.detail._records_cache:
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
            for rec in self.win.detail._records_cache:
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
                self.win.toast("Label did not match — delete cancelled")
                return
            self.win._op_start()

            def _work() -> None:
                from odoo_vite.core import odoo_inspect

                res = odoo_inspect.delete_record(client, model, record_id,
                                                 expected)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                if res.ok:
                    try:
                        off = self.win.detail._records_offset
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
            dlg.choose(self.win, None,
                       lambda d, t: _on_ok(_finish_alert(d, t, "cancel") == "ok"))
        else:
            self.win.toast("Delete needs libadwaita dialogs")

    # --------------------------------------------------- cron/export


    def _cron_refresh_flow(self, instance_id: str) -> None:
        client = self._dev_client(instance_id)
        if client is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import odoo_inspect

            res = odoo_inspect.list_cron_jobs(client)
            GLib.idle_add(self._show_crons, instance_id, res.ok,
                           res.data.get("crons", []) if res.ok else [],
                           "" if res.ok else res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _show_crons(self, instance_id: str, ok: bool, crons: list,
                    error: str) -> bool:
        self.win._op_end()
        if self.win.detail.instance_id == instance_id:
            try:
                if ok:
                    self.win.detail.set_crons(crons)
                else:
                    self.win.detail.set_rpc_status(error)
            except Exception:
                pass
        return False


    def _launch_json_flow(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import devtools_export

            res = devtools_export.generate_launch_json(inst)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()


    def _open_editor_flow(self, instance_id: str, editor: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import devtools_export

            res = devtools_export.open_in_editor(editor, inst.path)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)

        threading.Thread(target=_work, daemon=True).start()

    # ----------------------------------- Sprint 11 live control


