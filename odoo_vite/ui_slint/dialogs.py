"""Slint dialog/progress/toast drivers (PSS-2 dialogs, PSS-4 pickers).

Each .slint file loads once per process (compile cost paid once);
instantiation is cheap and headless-testable. Blocking modal exec() has
no Slint equivalent — callers show the component and continue on the
confirmed/cancelled callbacks (or a threading.Event for worker flows).

List dialogs (discover/schedules/files) embed the shared SelectionList;
each driver owns a SelectionState and pushes view_rows(), exactly like
the AppWindow sidebar does.
"""

from __future__ import annotations

import functools
import threading
import weakref
from datetime import timedelta
from pathlib import Path

import slint

from odoo_vite.ui_slint.databases import group_discover_entries
from odoo_vite.ui_slint.selection import SelectionState, sync_model
from odoo_vite.core.version import __version__


def _dcb(ref, name, *args):
    """Weak view callback (bridge _wcb parity): Slint-held callables must
    never strongly own their driver, or view↔driver refcount cycles form
    and cyclic GC may free Rust values on worker threads (Send abort).
    With weak refs, teardown is plain refcounting on the dropping thread
    (main, by construction) — no GC involvement, ever."""
    self_ = ref()
    if self_ is not None:
        getattr(self_, name)(*args)

_DIR = Path(__file__).resolve().parent
_cache: dict[str, object] = {}


def typed_gate_ok(text: str, expected: str) -> bool:
    """Exact-match rule for the destructive tier (Qt parity, incl. strip)."""
    return text.strip() == expected


def _load(filename: str):
    if filename not in _cache:
        _cache[filename] = slint.load_file(str(_DIR / filename))
    return _cache[filename]


class ConfirmDriver:
    """Light confirm tier. Result via on_result[bool] (True = confirmed)."""

    def __init__(self, heading: str, body: str = "",
                 confirm_label: str = "Confirm", destructive: bool = False,
                 on_result=None) -> None:
        module = _load("confirm_dialog.slint")
        self.view = module.ConfirmDialog()
        self.view.heading = heading
        self.view.body = body
        self.view.confirm_label = confirm_label
        self.view.destructive = destructive
        self._on_result = on_result
        me = weakref.ref(self)
        self.view.confirmed = functools.partial(_dcb, me, "_finish", True)
        self.view.cancelled = functools.partial(_dcb, me, "_finish", False)

    def _finish(self, confirmed: bool) -> None:
        if self._on_result is not None:
            self._on_result(confirmed)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class TypedConfirmDriver:
    """Destructive tier: OK enables only when entry matches expected."""

    def __init__(self, heading: str, body: str, expected: str,
                 confirm_label: str = "Delete", on_result=None) -> None:
        module = _load("typed_confirm_dialog.slint")
        self.view = module.TypedConfirmDialog()
        self.view.heading = heading
        self.view.body = body
        self.view.expected = expected
        self.view.confirm_label = confirm_label
        self._on_result = on_result
        me = weakref.ref(self)
        self.view.confirmed = functools.partial(_dcb, me, "_finish", True)
        self.view.cancelled = functools.partial(_dcb, me, "_finish", False)

    def gate_ok(self) -> bool:
        """Python-side gate (strip parity); the view enforces exact match."""
        return typed_gate_ok(self.view.entry, self.view.expected)

    def _finish(self, confirmed: bool) -> None:
        if self._on_result is not None:
            self._on_result(confirmed)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class ProgressDriver:
    """Streaming progress (Qt ProgressDialog parity): log view, cancel,
    close-at-end. Worker threads call append() via loop.call_soon_threadsafe
    or asyncio.to_thread patterns — never touch .view off-thread."""

    MAX_LINES = 500

    def __init__(self, title: str = "Working…") -> None:
        module = _load("progress_view.slint")
        self.view = module.ProgressView()
        self.view.status = title
        self.cancel_event = threading.Event()
        self._lines: list[str] = []
        self._on_close = None
        self.view.cancel_requested = functools.partial(
            _dcb, weakref.ref(self), "_on_cancel")
        self.view.closed = functools.partial(
            _dcb, weakref.ref(self), "_fire_close")

    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self.view.status = "Cancelling… (finishing current step)"

    def _fire_close(self) -> None:
        if self._on_close is not None:
            self._on_close()

    @property
    def on_close(self):
        return self._on_close

    @on_close.setter
    def on_close(self, fn) -> None:
        self._on_close = fn

    def append(self, line: str) -> None:
        self._lines.append(line)
        del self._lines[:max(0, len(self._lines) - self.MAX_LINES)]
        self.view.log = "\n".join(self._lines)
        self.view.status = line[-120:]

    def done(self, ok: bool, message: str) -> None:
        self.append(("Done: " if ok else "FAILED: ") + message)
        self.view.finished = True

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class ToastDriver:
    """Transient feedback (Qt Toaster parity): coalesced per driver —
    rapid shows replace the text and restart the hide timer."""

    def __init__(self, timeout_ms: int = 4000) -> None:
        module = _load("toast_overlay.slint")
        self.view = module.ToastOverlay()
        self._timer = slint.Timer()
        self._timeout = timedelta(milliseconds=timeout_ms)

    def show(self, message: str) -> None:
        self.view.message = message
        self.view.showing = True
        self._timer.start(slint.TimerMode.SingleShot, self._timeout,
                          functools.partial(
                              _dcb, weakref.ref(self), "hide"))

    def hide(self) -> None:
        if self.view is not None:
            self.view.showing = False

    def dismiss(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        self.hide()
        self.view = None


class CloneDialogDriver:
    """Name + port picker (Qt clone_dialog parity). confirmed() delivers
    (name, port) via on_confirm; empty names never deliver."""

    def __init__(self, source_name: str, default_name: str,
                 default_port: int, on_confirm=None) -> None:
        module = _load("clone_dialog.slint")
        self.view = module.CloneDialog()
        self.view.source_name = source_name
        self.view.new_name = default_name
        self.view.new_port = default_port
        self._on_confirm = on_confirm
        me = weakref.ref(self)
        self.view.confirmed = functools.partial(_dcb, me, "_on_ok")
        self.view.cancelled = lambda: None

    def _on_ok(self) -> None:
        name = self.view.new_name.strip()
        if name and self._on_confirm is not None:
            self._on_confirm(name, int(self.view.new_port))

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


# ---------------------------------------------------------- PSS-4 pickers

def _push(state: SelectionState, driver, model_attr: str = "_model",
          rows_prop: str = "rows", counts_prop: str = "counts_text") -> None:
    """Sync the driver's persistent model (created once, assigned once).

    Never mutate `view.rows` in place: the getter returns a read-only
    wrapper (beta), only Python-held ListModels accept writes. Multi-list
    dialogs (deps viewer) pass their own attrs; single-list callers keep
    the defaults.
    """
    rows = state.view_rows()
    model = getattr(driver, model_attr)
    if model is None:
        model = slint.ListModel(rows)
        setattr(driver, model_attr, model)
        setattr(driver.view, rows_prop, model)
    else:
        sync_model(model, rows)
    setattr(driver.view, counts_prop, state.counts_text())


class DiscoverDriver:
    """Untracked-database picker (A.1 parity: Likely grouped + pre-checked)."""

    def __init__(self, entries: list, instance_version: str,
                 on_track=None, on_message=None) -> None:
        groups = group_discover_entries(entries, instance_version)
        self._state = SelectionState(multi=True)
        rows = []
        for name in groups["likely"]:
            rows.append({"id": name, "title": name, "group": "Likely",
                         "checked": True})
        for name in groups["other"]:
            rows.append({"id": name, "title": name, "group": "Other versions"})
        for name in groups["plain"]:
            rows.append({"id": name, "title": name,
                         "group": "Uninitialized"})
        self._state.set_items(rows)
        module = _load("discover_dialog.slint")
        self.view = module.DiscoverDialog()
        self._model = None
        self._on_track = on_track
        self._on_message = on_message or (lambda _m: None)
        me = weakref.ref(self)
        self.view.toggled = functools.partial(_dcb, me, "_on_toggled")
        self.view.filter_changed = functools.partial(
            _dcb, me, "_on_filter")
        self.view.track_requested = functools.partial(_dcb, me, "_on_go")
        self.view.cancelled = lambda: None
        _push(self._state, self)

    def _on_toggled(self, item_id: str) -> None:
        for row in self._state._rows:
            if row["id"] == item_id and not row.get("header"):
                self._state.set_checked(item_id, not row.get("checked"))
                break
        _push(self._state, self)

    def _on_filter(self, text: str) -> None:
        self._state.set_filter(text)
        _push(self._state, self)

    def _on_go(self) -> None:
        picked = self._state.checked_ids()
        if not picked:
            self._on_message("Pick at least one database")
            return
        if self._on_track is not None:
            self._on_track(picked)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


PRESETS = [
    ("Daily 02:00", "0 2 * * *"),
    ("Hourly", "0 * * * *"),
    ("Weekly Sun 03:00", "0 3 * * 0"),
    ("Custom…", ""),
]


class ScheduleDriver:
    """Backup schedule editor (Qt schedule-dialog parity)."""

    def __init__(self, db_names: list, existing=None, on_save=None,
                 on_message=None) -> None:
        checked = set(getattr(existing, "databases", None) or db_names)
        self._state = SelectionState(multi=True)
        self._state.set_items([
            {"id": n, "title": n, "checked": n in checked}
            for n in db_names])
        module = _load("schedule_dialog.slint")
        self.view = module.ScheduleDialog()
        self.view.is_edit = existing is not None
        self._model = None
        self._save_cb = on_save
        self._on_message = on_message or (lambda _m: None)
        self.view.presets = slint.ListModel([label for label, _ in PRESETS])
        start_cron = (getattr(existing, "cron", None) or "0 2 * * *")
        self.view.cron = start_cron
        self.view.preset_idx = self._preset_index(start_cron)
        self.view.retention_n = int(getattr(existing, "retention_n", 7) or 7)
        self.view.retention_days = int(
            getattr(existing, "retention_days", 0) or 0)
        me = weakref.ref(self)
        self.view.db_toggled = functools.partial(_dcb, me, "_on_toggled")
        self.view.preset_chosen = functools.partial(_dcb, me, "_on_preset")
        self.view.cron_edited = functools.partial(_dcb, me, "_on_cron_edit")
        self.view.save_requested = functools.partial(_dcb, me, "_on_save")
        self.view.cancelled = lambda: None
        _push(self._state, self)

    @staticmethod
    def _preset_index(cron: str) -> int:
        for i, (_, expr) in enumerate(PRESETS):
            if expr and expr == cron:
                return i
        return len(PRESETS) - 1  # Custom…

    def _on_toggled(self, item_id: str) -> None:
        for row in self._state._rows:
            if row["id"] == item_id and not row.get("header"):
                self._state.set_checked(item_id, not row.get("checked"))
                break
        _push(self._state, self)

    def _on_preset(self, idx: int) -> None:
        expr = PRESETS[int(idx)][1]
        if expr:
            self.view.cron = expr

    def _on_cron_edit(self, text: str) -> None:
        # Custom typing flips the picker back (Qt clobber-bug fix, kept).
        # Set explicitly: headless callers bypass the LineEdit two-way bind.
        self.view.cron = text
        if self._preset_index(text) == len(PRESETS) - 1:
            self.view.preset_idx = len(PRESETS) - 1

    def _on_save(self) -> None:
        picked = self._state.checked_ids()
        if not picked:
            self._on_message("Pick at least one database")
            return
        cron = self.view.cron.strip()
        if not cron:
            self._on_message("Schedule needs a cron expression")
            return
        if self._save_cb is not None:
            self._save_cb({
                "databases": picked,
                "cron": cron,
                "retention_n": int(self.view.retention_n),
                "retention_days": int(self.view.retention_days),
            })

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class DepsDriver:
    """Dependency viewer (PSS-5a): two read-only shared lists.

    Single-select states with no filter; empty panes get explicit empty
    states instead of blank voids.
    """

    def __init__(self, module_name: str, depends: list,
                 required_by: list) -> None:
        self._dep_state = SelectionState(multi=False)
        self._dep_state.set_items(
            [{"id": n, "title": n} for n in depends])
        self._req_state = SelectionState(multi=False)
        self._req_state.set_items(
            [{"id": n, "title": n} for n in required_by])
        module = _load("deps_dialog.slint")
        self.view = module.DepsDialog()
        self.view.mod_name = module_name
        self._model_a = None
        self._model_b = None
        self.view.cancelled = lambda: None
        _push(self._dep_state, self, "_model_a", "depends_rows",
              "depends_counts")
        _push(self._req_state, self, "_model_b", "required_rows",
              "required_counts")
        if not depends:
            self.view.depends_counts = "No dependencies"
        if not required_by:
            self.view.required_counts = "Nothing requires it"

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class FilesDriver:
    """Backup-file browser (Restore/Delete on the picked file)."""

    def __init__(self, files: list, on_restore=None, on_delete=None,
                 on_message=None) -> None:
        self._state = SelectionState(multi=False)
        self._state.set_items([
            {"id": f.get("path", ""), "title": f.get("name", ""),
             "badge": f.get("detail", "")} for f in files])
        module = _load("files_dialog.slint")
        self.view = module.FilesDialog()
        self._model = None
        self._picked: str | None = None
        self._on_restore = on_restore
        self._on_delete = on_delete
        self._on_message = on_message or (lambda _m: None)
        me = weakref.ref(self)
        self.view.picked = functools.partial(_dcb, me, "_on_pick")
        self.view.restore_requested = functools.partial(
            _dcb, me, "_restore_clicked")
        self.view.delete_requested = functools.partial(
            _dcb, me, "_delete_clicked")
        self.view.cancelled = lambda: None
        _push(self._state, self)

    def _restore_clicked(self) -> None:
        self._act("restore", self._on_restore)

    def _delete_clicked(self) -> None:
        self._act("delete", self._on_delete)

    def _on_pick(self, item_id: str) -> None:
        self._picked = str(item_id)
        self._state.move_current(item_id)

    def _act(self, what: str, fn) -> None:
        if not self._picked:
            self._on_message(f"Pick a file to {what} first")
            return
        if fn is not None:
            fn(self._picked)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class AddonsDriver:
    """Addon Path Manager (PSS-5b): ordered entries with enable toggles.

    Entries are the source of truth (Qt _sync_back parity is structural:
    toggles mutate entries immediately, the state rebuilds from them with
    explicit flags on every refresh).
    """

    def __init__(self, entries: list, on_apply=None, on_message=None,
                 on_browse=None) -> None:
        self._entries = [dict(e) for e in entries]
        self._state = SelectionState(multi=True)
        self._picked: str | None = None
        module = _load("addons_dialog.slint")
        self.view = module.AddonsDialog()
        self._model = None
        self._apply_cb = on_apply
        self._on_message = on_message or (lambda _m: None)
        self._browse_cb = on_browse
        me = weakref.ref(self)
        self.view.toggled = functools.partial(_dcb, me, "_on_toggled")
        self.view.picked = functools.partial(_dcb, me, "_on_pick")
        self.view.rename_requested = functools.partial(
            _dcb, me, "_on_rename")
        self.view.up_requested = functools.partial(_dcb, me, "_move_up")
        self.view.down_requested = functools.partial(
            _dcb, me, "_move_down")
        self.view.remove_requested = functools.partial(
            _dcb, me, "_on_remove")
        self.view.browse_requested = functools.partial(
            _dcb, me, "_on_browse")
        self.view.add_requested = functools.partial(_dcb, me, "_on_add")
        self.view.apply_requested = functools.partial(
            _dcb, me, "_on_apply")
        self.view.cancelled = lambda: None
        self._refresh()

    def _refresh(self) -> None:
        self._state.set_items([
            {"id": e["path"], "title": e["path"],
             "badge": "" if e.get("enabled", True) else "disabled",
             "checked": bool(e.get("enabled", True))}
            for e in self._entries])
        if self._picked not in [e["path"] for e in self._entries]:
            self._picked = None
        _push(self._state, self)
        if self.view is not None:
            self.view.selected_id = self._picked or ""
            if self._picked:
                self.view.edit_path = self._picked

    def set_add_path(self, path: str) -> None:
        if self.view is not None:
            self.view.add_path = path or ""

    def _on_toggled(self, item_id: str) -> None:
        for entry in self._entries:
            if entry["path"] == item_id:
                entry["enabled"] = not entry.get("enabled", True)
                break
        self._refresh()

    def _on_pick(self, item_id: str) -> None:
        self._picked = str(item_id)
        self._state.move_current(item_id)
        self._refresh()

    def _on_rename(self) -> None:
        if not self._picked:
            self._on_message("Pick a path to rename first")
            return
        new = self.view.edit_path.strip()
        if not new:
            self._on_message("Type a new path first")
            return
        if new == self._picked:
            return
        if new in [e["path"] for e in self._entries]:
            self._on_message("Already in the list")
            return
        for entry in self._entries:
            if entry["path"] == self._picked:
                entry["path"] = new
        self._picked = new
        self._refresh()

    def _move_up(self) -> None:
        self._move(-1)

    def _move_down(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        if not self._picked:
            self._on_message("Pick a path to move first")
            return
        idx = next((i for i, e in enumerate(self._entries)
                    if e["path"] == self._picked), None)
        if idx is None:
            return
        other = idx + delta
        if 0 <= other < len(self._entries):
            self._entries[idx], self._entries[other] = \
                self._entries[other], self._entries[idx]
            self._refresh()

    def _on_remove(self) -> None:
        if not self._picked:
            self._on_message("Pick a path to remove first")
            return
        self._entries[:] = [e for e in self._entries
                            if e["path"] != self._picked]
        self._picked = None
        self._refresh()

    def _on_browse(self) -> None:
        if self._browse_cb is not None:
            self._browse_cb()

    def _on_add(self) -> None:
        path = (self.view.add_path or "").strip()
        if not path:
            self._on_message("Browse for a folder first")
            return
        if path in [e["path"] for e in self._entries]:
            self._on_message("Already in the list")
            return
        self._entries.append({"path": path, "enabled": True})
        self.view.add_path = ""
        self._refresh()

    def _on_apply(self) -> None:
        if self._apply_cb is not None:
            self._apply_cb([dict(e) for e in self._entries])

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class RecordDriver:
    """Record editor (PSS-6b): metadata-driven field form.

    Fields are fixed at open time; the view owns entry text one-way and
    reports edits — values live here and deliver on save.
    """

    def __init__(self, title: str, fields: list, initial: dict,
                 on_save=None) -> None:
        self._values = {name: str(initial.get(name, "") or "")
                        for name in fields}
        module = _load("record_dialog.slint")
        self.view = module.RecordDialog()
        self.view.rec_title = title
        self.view.fields = slint.ListModel(
            [{"name": name, "value": self._values[name]}
             for name in fields])
        self._save_cb = on_save
        me = weakref.ref(self)
        self.view.field_edited = functools.partial(
            _dcb, me, "_on_field_edit")
        self.view.save_requested = functools.partial(_dcb, me, "_on_save")
        self.view.cancelled = lambda: None

    def _on_field_edit(self, name: str, text: str) -> None:
        if name in self._values:
            self._values[str(name)] = str(text)

    def _on_save(self) -> None:
        if self._save_cb is not None:
            self._save_cb(dict(self._values))

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class AboutDriver:
    """About dialog (PSS-9): hosts the AboutSlint royalty-free
    disclosure widget. No logic — show, close, release."""

    def __init__(self) -> None:
        module = _load("about_dialog.slint")
        self.view = module.AboutDialog()
        self.view.app_version = __version__
        self.view.cancelled = lambda: None

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class CreateDriver:
    """New-instance wizard driver (PSS-7a): page stack state + validation.

    Version → syscheck → details → provision. Branch loading and
    syschecking round-trip through bridge hooks (worker results arrive on
    the queue); details validation is pure and runs here on the UI
    thread. Provision streams through the bridge queue like progress
    dialogs; Retry reuses the draft, Discard removes it.
    Cancel closes the dialog and leaves any draft in place (Qt parity —
    drafts stay removable from the sidebar).
    """

    MAX_LINES = 500

    def __init__(self, on_load_branches=None, on_run_syscheck=None,
                 on_provision=None, on_discard=None,
                 on_close=None) -> None:
        from odoo_vite.ui_slint.wizards import (
            build_draft,
            generate_password,
            refresh_draft,
            validate_details,
        )

        self._build_draft = build_draft
        self._refresh_draft = refresh_draft
        self._validate_details = validate_details
        self._generate_password = generate_password
        self._branches = SelectionState(multi=False)
        self._version = ""
        self._draft = None
        self._page = 0
        self.cancel_event = threading.Event()
        self._lines: list[str] = []
        self._model = None
        self._on_load_branches = on_load_branches
        self._on_run_syscheck = on_run_syscheck
        self._on_provision = on_provision
        self._discard_cb = on_discard
        self._on_close = on_close
        module = _load("create_dialog.slint")
        self.view = module.CreateDialog()
        me = weakref.ref(self)
        self.view.branch_picked = functools.partial(
            _dcb, me, "_on_branch_pick")
        self.view.branches_refresh = functools.partial(
            _dcb, me, "_on_branches_refresh")
        self.view.gen_password = functools.partial(_dcb, me, "_on_gen")
        self.view.wiz_nav = functools.partial(_dcb, me, "_on_nav")
        self.view.prov_cancel = functools.partial(_dcb, me, "_on_cancel")
        self.view.prov_retry = functools.partial(_dcb, me, "_on_retry")
        self.view.prov_discard = functools.partial(
            _dcb, me, "_on_discard")
        self.view.cancelled = lambda: None
        self._push_branches()
        if self._on_load_branches is not None:
            self._on_load_branches()

    # ------------------------------------------------------------ navigation

    def _goto(self, page: int) -> None:
        self._page = page
        self.view.page_idx = page
        if page == 1 and self._on_run_syscheck is not None:
            self.view.syscheck_status = (
                f"Checking requirements for Odoo {self._version}…")
            self._on_run_syscheck(self._version)

    def _on_nav(self, where: str) -> None:
        if where == "back":
            if self._page > 0 and not self.view.prov_running:
                self._goto(self._page - 1)
        elif where == "next":
            self._on_next()
        elif where in ("cancel", "close"):
            if self._on_close is not None:
                self._on_close()

    def _on_next(self) -> None:
        if self._page == 0:
            if not self._version:
                self.view.branch_status = "Pick a version branch first."
                return
            self._goto(1)
        elif self._page == 1:
            self._goto(2)  # advisory — warnings never block
        elif self._page == 2:
            err = self._validate_details(self._collect_details())
            self.view.det_err = err or ""
            if err is None:
                self._goto(3)
                self.begin_provision()

    # ---------------------------------------------------------------- inputs

    def _on_branch_pick(self, branch: str) -> None:
        self._version = str(branch)
        self._branches.move_current(branch)
        self._push_branches()

    def _on_branches_refresh(self) -> None:
        self.view.branch_status = "Loading branches…"
        if self._on_load_branches is not None:
            self._on_load_branches()

    def _push_branches(self) -> None:
        _push(self._branches, self, "_model", "branch_rows",
              "branch_counts")
        if self.view is not None:
            self.view.branch_selected = self._version

    def set_branches(self, branches: list, message: str) -> None:
        self._branches.set_items(
            [{"id": b, "title": b} for b in branches])
        if self._version and not any(
                r["id"] == self._version and not r.get("header")
                for r in self._branches._rows):
            self._version = ""
        self._push_branches()
        if self.view is not None:
            self.view.branch_status = message or (
                f"{len(branches)} branches" if branches else
                "No branches listed.")

    def set_syscheck(self, checks: list, message: str) -> None:
        if self.view is None:
            return
        self.view.syscheck_lines = slint.ListModel([
            f"{'✅' if c.get('ok') else '⚠'} {c.get('name', '?')}: "
            f"{c.get('detail', '')}"
            for c in checks or []])
        self.view.syscheck_status = message

    def _on_gen(self) -> None:
        self.view.det_dbpass = self._generate_password()

    def _collect_details(self) -> dict:
        return {
            "name": (self.view.det_name or "").strip(),
            "port": int(self.view.det_port or 0),
            "db_user": (self.view.det_dbuser or "").strip() or "odoo",
            "db_password": self.view.det_dbpass or "",
            "plaintext": bool(self.view.det_plaintext),
            "db_name": (self.view.det_dbname or "").strip(),
        }

    # ------------------------------------------------------------- provision

    def begin_provision(self) -> None:
        details = self._collect_details()
        if self._draft is None:
            self._draft = self._build_draft(details, self._version)
        else:
            self._draft = self._refresh_draft(
                self._draft, details, self._version)
        self.cancel_event.clear()
        self._lines = []
        self.view.prov_log = ""
        self.view.prov_status = "Provisioning…"
        self.view.prov_running = True
        self.view.prov_failed = False
        self.view.prov_done = False
        if self._on_provision is not None:
            self._on_provision(self._draft, details.get("plaintext",
                                                        False))

    def append_log(self, line: str) -> None:
        self._lines.append(line)
        del self._lines[:max(0, len(self._lines) - self.MAX_LINES)]
        if self.view is not None:
            self.view.prov_log = "\n".join(self._lines)
            self.view.prov_status = line[-120:]

    def prov_done(self, ok: bool, message: str, failed_step: str) -> None:
        if self.view is None:
            return
        self.view.prov_running = False
        if ok:
            self.view.prov_done = True
            self.view.prov_status = "Instance ready ✓"
        else:
            self.view.prov_failed = True
            self.view.prov_status = f"Failed at step: {failed_step}"
            self.append_log(f"ERROR: {message}")

    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self.view.prov_status = "Cancelling… (waiting on subprocess)"

    def _on_retry(self) -> None:
        self.begin_provision()

    def _on_discard(self) -> None:
        if self._draft is None:
            if self._on_close is not None:
                self._on_close()
            return
        if self._discard_cb is not None:
            self._discard_cb(self._draft.id)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class AdoptDriver:
    """Adopt-existing-install wizard driver (PSS-7b).

    Locate reparses live (pure, UI thread); gaps rebuild on entering page
    1 with editable entries for missing fields; Adopt runs through the
    bridge queue. Nothing is written until Adopt (core guarantee).
    """

    def __init__(self, on_browse_conf=None, on_browse_community=None,
                 on_adopt=None, on_close=None) -> None:
        from odoo_vite.ui_slint.wizards import (
            build_adopt_overrides,
            gap_rows,
            parse_adopt_paths,
            suggest_db_name,
            validate_locate,
        )

        self._gap_rows = gap_rows
        self._parse = parse_adopt_paths
        self._validate_locate = validate_locate
        self._suggest_db = suggest_db_name
        self._build_overrides = build_adopt_overrides
        self._parsed: dict = {}
        self._report: dict = {}
        self._values: dict = {}
        self._page = 0
        self._browse_conf_cb = on_browse_conf
        self._browse_community_cb = on_browse_community
        self._on_adopt = on_adopt
        self._close_cb = on_close
        module = _load("adopt_dialog.slint")
        self.view = module.AdoptDialog()
        me = weakref.ref(self)
        self.view.locate_changed = functools.partial(
            _dcb, me, "_on_locate_changed")
        self.view.browse_conf = functools.partial(
            _dcb, me, "_on_browse_conf")
        self.view.browse_community = functools.partial(
            _dcb, me, "_on_browse_community")
        self.view.gap_edited = functools.partial(
            _dcb, me, "_on_gap_edit")
        self.view.wiz_nav = functools.partial(_dcb, me, "_on_nav")
        self.view.cancelled = lambda: None
        self._reparse()

    # ---------------------------------------------------------------- locate

    def _locate(self) -> tuple[str, str, str]:
        return ((self.view.loc_name or "").strip(),
                (self.view.loc_conf or "").strip(),
                (self.view.loc_community or "").strip())

    def _reparse(self) -> None:
        _name, conf, community = self._locate()
        info = self._parse(conf, community)
        self._parsed = info["parsed"]
        self._report = info["report"]
        if self.view is not None:
            self.view.loc_detected = info["detected"]

    def _on_locate_changed(self) -> None:
        self._reparse()

    def _on_browse_conf(self) -> None:
        if self._browse_conf_cb is not None:
            self._browse_conf_cb()

    def _on_browse_community(self) -> None:
        if self._browse_community_cb is not None:
            self._browse_community_cb()

    def set_conf_path(self, path: str) -> None:
        if self.view is not None and path:
            self.view.loc_conf = path
            self._reparse()

    def set_community_path(self, path: str) -> None:
        if self.view is not None and path:
            self.view.loc_community = path
            self._reparse()

    # ------------------------------------------------------------------- nav

    def _goto(self, page: int) -> None:
        self._page = page
        self.view.page_idx = page
        if page == 1:
            self._rebuild_gaps()

    def _on_nav(self, where: str) -> None:
        if where == "back":
            if self._page > 0 and not (
                    self._page == 2 and not self.view.run_done):
                self._goto(self._page - 1)
        elif where == "next":
            self._on_next()
        elif where in ("cancel", "close"):
            if self._close_cb is not None:
                self._close_cb()

    def _on_next(self) -> None:
        if self._page == 0:
            name, conf, community = self._locate()
            err = self._validate_locate(name, conf, community)
            self.view.loc_err = err or ""
            if err is None:
                self._reparse()
                self._goto(1)
        elif self._page == 1:
            if not (self.view.gap_db or "").strip():
                self.view.gap_err = "Primary database is required."
                return
            self.view.gap_err = ""
            self._goto(2)
            self._begin_adopt()

    # ------------------------------------------------------------------- gaps

    def _rebuild_gaps(self) -> None:
        rows = self._gap_rows(self._parsed, self._report)
        self._values = {r["key"]: r["value"] for r in rows
                        if r["missing"]}
        if self.view is None:
            return
        self.view.gap_rows = slint.ListModel([
            {"key": r["key"], "caption": r["caption"],
             "status": r["status"], "missing": r["missing"],
             "value": r["value"]} for r in rows])
        if not (self.view.gap_db or "").strip():
            name, _c, _m = self._locate()
            self.view.gap_db = self._suggest_db(name)

    def _on_gap_edit(self, key: str, text: str) -> None:
        if key in self._values:
            self._values[str(key)] = str(text)

    # -------------------------------------------------------------------- run

    def _begin_adopt(self) -> None:
        name, conf, community = self._locate()
        overrides = self._build_overrides(
            self._parsed, dict(self._values),
            (self.view.gap_db or "").strip())
        self.view.run_status = f"Adopting {name}…"
        self.view.run_done = False
        if self._on_adopt is not None:
            self._on_adopt({"name": name, "conf": conf,
                            "community": community,
                            "overrides": overrides})

    def adopt_done(self, ok: bool, message: str) -> None:
        if self.view is None:
            return
        self.view.run_done = True
        self.view.run_status = ("Adopted ✓ (no files touched)" if ok
                                else f"Adopt failed: {message}")

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class ScaffoldDriver:
    """New-module wizard driver (PSS-7c): definition form → build log.

    Instances arrive as plain entries (id/name/custom_addons/primary)
    so the driver never touches the registry; the bridge resolves ids.
    Validation is pure except the database-exists check, which runs in
    the build worker before generating (never auto-creates).
    """

    MAX_LINES = 500

    def __init__(self, instances: list, on_browse_dest=None,
                 on_build=None, on_close=None) -> None:
        from odoo_vite.ui_slint.wizards import (
            build_scaffold_definition,
            validate_scaffold,
        )

        self._build_definition = build_scaffold_definition
        self._validate = validate_scaffold
        self._instances = [dict(e) for e in instances]
        self._page = 0
        self._lines: list[str] = []
        self._browse_dest_cb = on_browse_dest
        self._build_cb = on_build
        self._close_cb = on_close
        module = _load("scaffold_dialog.slint")
        self.view = module.ScaffoldDialog()
        self.view.scaf_instances = slint.ListModel(
            [e["name"] for e in self._instances])
        if self._instances:
            first = self._instances[0]
            self.view.scaf_dest = first.get("custom") or ""
            if first.get("primary"):
                self.view.scaf_db = ""
        me = weakref.ref(self)
        self.view.browse_dest = functools.partial(
            _dcb, me, "_on_browse_dest")
        self.view.wiz_nav = functools.partial(_dcb, me, "_on_nav")
        self.view.cancelled = lambda: None

    def _values(self) -> dict:
        return {
            "tech": (self.view.scaf_tech or "").strip(),
            "pretty": (self.view.scaf_pretty or "").strip(),
            "version": (self.view.scaf_version or "").strip(),
            "summary": (self.view.scaf_summary or "").strip(),
            "author": (self.view.scaf_author or "").strip(),
            "model": (self.view.scaf_model or "").strip(),
            "fields_text": self.view.scaf_fields or "",
            "dest": (self.view.scaf_dest or "").strip(),
            "db": (self.view.scaf_db or "").strip(),
        }

    def _selected_instance(self):
        try:
            return self._instances[int(self.view.scaf_inst_idx)]
        except (IndexError, TypeError, ValueError):
            return None

    def _on_nav(self, where: str) -> None:
        if where == "back":
            if self._page > 0 and not self.view.build_running:
                self._page = 0
                self.view.page_idx = 0
        elif where == "next":
            values = self._values()
            err = self._validate(values, bool(self._instances))
            self.view.scaf_err = err or ""
            if err is None:
                self._page = 1
                self.view.page_idx = 1
                self._begin_build(values)
        elif where in ("cancel", "close"):
            if self._close_cb is not None:
                self._close_cb()

    def _on_browse_dest(self) -> None:
        if self._browse_dest_cb is not None:
            self._browse_dest_cb()

    def set_dest(self, path: str) -> None:
        if self.view is not None and path:
            self.view.scaf_dest = path

    def _begin_build(self, values: dict) -> None:
        entry = self._selected_instance()
        if entry is None:
            self.view.scaf_err = "No instance available for acceptance."
            self._page = 0
            self.view.page_idx = 0
            return
        definition = self._build_definition(values)
        self._lines = []
        self.view.build_log = ""
        self.view.build_status = "Generating…"
        self.view.build_running = True
        self.view.build_failed = False
        self.view.build_done = False
        if self._build_cb is not None:
            self._build_cb(definition, values["dest"], entry["id"],
                           values["db"])

    def append_log(self, line: str) -> None:
        self._lines.append(line)
        del self._lines[:max(0, len(self._lines) - self.MAX_LINES)]
        if self.view is not None:
            self.view.build_log = "\n".join(self._lines)
            self.view.build_status = line[-120:]

    def build_done(self, ok: bool, message: str) -> None:
        if self.view is None:
            return
        self.view.build_running = False
        if ok:
            self.view.build_done = True
            self.view.build_status = "Installed cleanly ✓"
        else:
            self.view.build_failed = True
            self.append_log(f"ERROR: {message}")
            self.view.build_status = "Failed"

    def prov_done(self, ok: bool, message: str,
                  _failed_step: str = "") -> None:
        """CreateDriver-shaped completion so the shared wiz-done drain
        serves both wizards (the step has no Scaffold equivalent)."""
        self.build_done(ok, message)

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class PreferencesDriver:
    """Preferences dialog driver (PSS-8): mode combo + environment
    status. Pure UI state — the bridge reads/saves synchronously."""

    def __init__(self, mode: str, keyring_text: str, db_path: str,
                 on_save=None) -> None:
        from odoo_vite.ui_slint.transfer import MODE_LABELS, MODE_NOTES

        module = _load("preferences_dialog.slint")
        self.view = module.PreferencesDialog()
        self.view.mode_labels = slint.ListModel(MODE_LABELS)
        try:
            idx = ["developer", "managed"].index(mode or "developer")
        except ValueError:
            idx = 0
        self.view.mode_idx = idx
        self.view.mode_note = MODE_NOTES[idx]
        self.view.keyring_text = keyring_text
        self.view.db_path = db_path
        self._save_cb = on_save
        me = weakref.ref(self)
        self.view.mode_changed = functools.partial(
            _dcb, me, "_on_mode_change")
        self.view.save_requested = functools.partial(_dcb, me, "_on_save")
        self.view.cancelled = lambda: None

    def _on_mode_change(self, idx: int) -> None:
        from odoo_vite.ui_slint.transfer import MODE_NOTES

        try:
            note = MODE_NOTES[int(idx)]
        except (IndexError, TypeError, ValueError):
            return
        # Set explicitly: headless callers bypass the ComboBox bind.
        self.view.mode_idx = int(idx)
        self.view.mode_note = note

    def selected_mode(self) -> str:
        try:
            return ["developer", "managed"][int(self.view.mode_idx)]
        except (IndexError, TypeError, ValueError):
            return "developer"

    def _on_save(self) -> None:
        if self._save_cb is not None:
            self._save_cb(self.selected_mode())

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None


class ImportDriver:
    """Import dialog driver (PSS-8): preview summary + new name/port."""

    def __init__(self, bundle_name: str, bundle_version: str,
                 default_name: str, default_port: int,
                 on_import=None) -> None:
        module = _load("transfer_dialog.slint")
        self.view = module.TransferDialog()
        self.view.bundle_name = bundle_name
        self.view.bundle_version = bundle_version
        self.view.new_name = default_name
        self.view.new_port = default_port
        self._import_cb = on_import
        me = weakref.ref(self)
        self.view.import_requested = functools.partial(
            _dcb, me, "_on_import")
        self.view.cancelled = lambda: None

    def _on_import(self) -> None:
        name = (self.view.new_name or "").strip()
        if not name:
            return
        if self._import_cb is not None:
            self._import_cb(name, int(self.view.new_port))

    def show(self) -> None:
        self.view.show()

    def dismiss(self) -> None:
        try:
            self.view.hide()
        except Exception:
            pass
        self.view = None
