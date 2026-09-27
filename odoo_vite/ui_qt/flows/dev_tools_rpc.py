"""DevTools RPC flows (PSQ-8.3a): faithful port of GTK dev_tools_rpc.py.

Connect, Model Inspector, Record Browser (typed-delete confirm,
diff-preview update, ir.* structurally undeletable via core — the
safety net ports with the same weight), Cron Jobs, launch.json,
editor-open. Sessions + per-instance browser state live here.
"""

from PySide6.QtCore import QObject, Signal

from odoo_vite.core import odoo_inspect, odoo_rpc
from odoo_vite.core.registry import get_instance
from odoo_vite.core.result import Result
from odoo_vite.ui_qt.widgets.dialogs import ask_confirm, ask_confirm_typed
from odoo_vite.ui_qt.workers import run_in_background

PAGE_SIZE = 50


def diff_record_values(current: dict, new: dict) -> dict:
    """Changed fields {key: (old, new)} — thin local copy of the GTK
    helper (ui_qt must not import GTK ui/); same stringified semantics."""
    current = current or {}
    changed = {}
    for key, value in (new or {}).items():
        if str(current.get(key)) != str(value):
            changed[key] = (current.get(key), value)
    return changed


class DevToolsRpcFlows(QObject):
    """Constructed once with the main window. All methods are GUI-safe."""

    message = Signal(str)
    rpcStatus = Signal(str, str)  # (instance_id, text)
    modelsReady = Signal(str, list)  # (instance_id, models)
    metadataReady = Signal(str, dict)  # (instance_id, meta)
    recordsReady = Signal(str, list, int, bool)  # (id, records, off, more)
    cronsReady = Signal(str, list)  # (instance_id, crons)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, dict] = {}
        self._browser: dict[str, dict] = {}
        self._last_meta: dict[str, dict] = {}

    # ------------------------------------------------------------ connect

    def _session(self, instance_id: str):
        return self._sessions.get(instance_id)

    def connect(self, instance_id: str, user: str = "",
                password: str = "", remember: bool = False) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        user = user or "admin"
        if not password:
            try:
                saved = odoo_rpc.recall_credentials(instance_id) or {}
                password = saved.get("password", "")
            except Exception:
                password = ""
        if not password:
            self.message.emit(
                "Enter the Odoo password first (never guessed, never stored "
                "unless Remember is checked)")
            self.rpcStatus.emit(instance_id, "Not connected: no password.")
            return
        self.message.emit("Connecting…")

        def _work():
            res = odoo_rpc.connect_instance(
                inst, db=None, odoo_user=user, odoo_password=password)
            if res.ok and remember:
                try:
                    odoo_rpc.remember_credentials(instance_id, user, password)
                except Exception:
                    pass
            return res

        def _done(ok: bool, message: str, data: dict) -> None:
            if ok:
                self._sessions[instance_id] = data
                self._browser.pop(instance_id, None)
                self.rpcStatus.emit(
                    instance_id,
                    f"Connected as {data.get('user')} "
                    f"(db {data.get('db')}).")
                self.message.emit("RPC connected")
            else:
                self.rpcStatus.emit(instance_id, f"Not connected: {message}")
                self.message.emit(message)

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ models

    def list_models(self, instance_id: str) -> None:
        client = self._session(instance_id)
        if client is None:
            self.message.emit("Connect RPC first")
            return
        self.message.emit("Listing models…")

        def _work():
            return odoo_inspect.list_models(client)

        def _done(ok: bool, message: str, data: dict) -> None:
            if ok:
                self.modelsReady.emit(instance_id,
                                      data.get("models", []))
            self.message.emit(message)

        run_in_background(self, _work, _done)

    def model_selected(self, instance_id: str, model: str) -> None:
        client = self._session(instance_id)
        if client is None or not model:
            return
        state = self._browser.setdefault(instance_id, {})
        state["model"] = model
        state["offset"] = 0
        self.message.emit("Loading fields…")

        def _work():
            return odoo_inspect.get_model_metadata(client, model)

        def _done(ok: bool, message: str, data: dict) -> None:
            if ok:
                self._last_meta[instance_id] = data
                self.metadataReady.emit(instance_id, data)
            else:
                self.message.emit(message)

        run_in_background(self, _work, _done)
        self.records_page(instance_id, 0)

    # ------------------------------------------------------------ records

    def _rec_state(self, instance_id: str) -> dict:
        return self._browser.setdefault(
            instance_id, {"model": "", "offset": 0, "cache": []})

    def records_page(self, instance_id: str, offset: int) -> None:
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            return
        offset = max(0, offset)
        self.message.emit("Loading records…")

        def _work():
            res = odoo_inspect.search_records(
                client, model, domain=state.get("domain"),
                fields=["id", "display_name", "name"],
                offset=offset, limit=PAGE_SIZE)
            if not res.ok:
                return res
            records = res.data if isinstance(res.data, list) else []
            return Result(ok=True, message=f"{len(records)} record(s)",
                          data={"records": records})

        def _done(ok: bool, message: str, data: dict) -> None:
            if not ok:
                self.message.emit(message)
                return
            records = data.get("records", [])
            state["offset"] = offset
            state["cache"] = records
            self.recordsReady.emit(instance_id, records, offset,
                                   len(records) >= PAGE_SIZE)

        run_in_background(self, _work, _done)

    def rec_search(self, instance_id: str, field: str, op: str,
                   value: str) -> None:
        state = self._rec_state(instance_id)
        field, value = (field or "").strip(), (value or "").strip()
        state["domain"] = ([[field, op, value]] if field else None)
        self.records_page(instance_id, 0)

    def rec_page(self, instance_id: str, delta: int) -> None:
        state = self._rec_state(instance_id)
        self.records_page(instance_id, state.get("offset", 0)
                           + delta * PAGE_SIZE)

    # ------------------------------------------------------------ record edit

    def record_dialog(self, parent_widget, instance_id: str, model: str,
                      record: dict | None) -> dict | None:
        """Field editor dialog. Returns values dict, or None if cancelled.

        Fields come from cached metadata when available, else the record's
        own keys. Relational blobs are skipped (same rule as GTK).
        """
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QVBoxLayout,
            QWidget,
        )

        fields = []
        meta = getattr(self, "_last_meta", {}).get(instance_id, {})
        for spec in meta.get("fields") or []:
            if not isinstance(spec, dict):
                continue
            if spec.get("type") in ("many2one", "one2many", "many2many"):
                continue
            fname = spec.get("name", "")
            if fname:
                fields.append(fname)
        if not fields and record:
            fields = [k for k in record.keys()
                      if k not in ("id", "__last_update")]
        dlg = QDialog(parent_widget if isinstance(parent_widget, QWidget)
                      else None)
        dlg.setWindowTitle(
            f"{'Edit' if record else 'New'} {model} "
            f"#{record.get('id') if record else ''}".strip())
        dlg.setMinimumWidth(480)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        edits: dict[str, QLineEdit] = {}
        for fname in fields:
            edit = QLineEdit()
            if record is not None:
                edit.setText(str(record.get(fname, "") or ""))
            form.addRow(fname + ":", edit)
            edits[fname] = edit
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return None
        return {fname: edit.text() for fname, edit in edits.items()}

    def rec_new(self, parent_widget, instance_id: str) -> None:
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        if client is None or not model:
            self.message.emit("Pick a model first")
            return
        values = self.record_dialog(parent_widget, instance_id, model, None)
        if not values:
            return
        self.message.emit("Creating…")

        def _work():
            return odoo_inspect.create_record(client, model, values)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            if ok:
                self.records_page(instance_id, state.get("offset", 0))

        run_in_background(self, _work, _done)

    def rec_edit(self, parent_widget, instance_id: str,
                 record_id: int) -> None:
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        record = next((r for r in state.get("cache", [])
                       if r.get("id") == record_id), None)
        if client is None or not model or record is None:
            self.message.emit("Select a record first")
            return
        values = self.record_dialog(parent_widget, instance_id, model,
                                    record)
        if not values:
            return
        # Before/after diff preview (non-negotiable safety net).
        changed = diff_record_values(record, values)
        if not changed:
            self.message.emit("No changes vs current values")
            return
        preview = "\n".join(f"{k}: {old!r} → {new!r}"
                            for k, (old, new) in changed.items())
        from PySide6.QtWidgets import QWidget
        if not ask_confirm(
                parent_widget if isinstance(parent_widget, QWidget) else None,
                f"Update {model} #{record_id}?",
                f"Changed fields:\n{preview}", "Apply update"):
            return
        self.message.emit("Updating…")

        def _work():
            return odoo_inspect.update_record(client, model, record_id,
                                              changed and
                                              {k: v for k, (_, v)
                                               in changed.items()})

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            if ok:
                self.records_page(instance_id, state.get("offset", 0))

        run_in_background(self, _work, _done)

    def rec_delete(self, parent_widget, instance_id: str,
                   record_id: int) -> None:
        client = self._session(instance_id)
        state = self._rec_state(instance_id)
        model = state.get("model", "")
        record = next((r for r in state.get("cache", [])
                       if r.get("id") == record_id), None)
        if client is None or not model or record is None:
            self.message.emit("Select a record first")
            return
        if model.startswith("ir."):
            # Structural belt-and-braces (core refuses too, no exceptions).
            self.message.emit(
                f"Refusing to delete from system model '{model}' — "
                "framework metadata rows are off-limits, no exceptions")
            return
        shown = record.get("display_name") or record.get("name", record_id)
        label = f"{model} '{shown}'"
        expected = str(record.get("display_name")
                       or record.get("name", "") or record_id)
        from PySide6.QtWidgets import QWidget
        if not ask_confirm_typed(
                parent_widget if isinstance(parent_widget, QWidget) else None,
                f"Delete {label}?",
                "This permanently deletes the record. Odoo itself may "
                "refuse when dependents block it — whatever it reports is "
                f"shown verbatim. There is no undo.\n\nType {expected!r} "
                "to confirm.",
                expected, "Delete permanently"):
            return
        self.message.emit("Deleting…")

        def _work():
            return odoo_inspect.delete_record(client, model, record_id,
                                              expected)

        def _done(ok: bool, message: str, _data: dict) -> None:
            self.message.emit(message)
            if ok:
                self.records_page(instance_id, state.get("offset", 0))

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ cron

    def cron_refresh(self, instance_id: str) -> None:
        client = self._session(instance_id)
        if client is None:
            self.message.emit("Connect RPC first")
            return
        self.message.emit("Loading cron jobs…")

        def _work():
            return odoo_inspect.list_cron_jobs(client)

        def _done(ok: bool, message: str, data: dict) -> None:
            if ok:
                self.cronsReady.emit(instance_id,
                                     data.get("crons", data.get("jobs", [])))
            self.message.emit(message)

        run_in_background(self, _work, _done)

    # ------------------------------------------------------------ launch/editors

    def launch_json(self, instance_id: str) -> None:
        from odoo_vite.core import devtools_export
        from odoo_vite.core.registry import get_instance

        inst = get_instance(instance_id)
        if inst is None:
            return
        res = devtools_export.generate_launch_json(inst)
        self.message.emit(res.message)

    def open_editor(self, instance_id: str, editor: str) -> None:
        from odoo_vite.core import devtools_export
        from odoo_vite.core.registry import get_instance

        inst = get_instance(instance_id)
        if inst is None:
            return
        folder = (inst.path or "").strip()
        if not folder:
            self.message.emit("Instance records no path")
            return
        res = devtools_export.open_in_editor(editor, folder)
        self.message.emit(res.message)
