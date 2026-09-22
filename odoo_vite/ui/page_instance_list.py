"""Instance list view (Sprint 3): live status pills + quick actions.

Rows update in place on every poll tick (no rebuild → no flicker).
Structural changes (add/remove) are applied without touching live rows.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from odoo_vite.core.registry import list_instances  # noqa: E402

PILL_CLASS = {
    "running": "status-running",
    "stopped": "status-stopped",
    "error": "status-error",
    "draft": "status-draft",
}


class InstanceRow(Gtk.ListBoxRow):
    """One instance row: name, status pill, version/port/stats, actions."""

    def __init__(self, status: dict, on_action=None) -> None:
        super().__init__()
        self.instance_id = status["id"]
        self._on_action = on_action
        self._pill_class: str | None = None

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        self.set_child(outer)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       hexpand=True)
        outer.append(text)
        self.lbl_name = Gtk.Label(xalign=0)
        self.lbl_name.add_css_class("heading")
        text.append(self.lbl_name)
        self.lbl_sub = Gtk.Label(xalign=0)
        self.lbl_sub.add_css_class("dim-label")
        text.append(self.lbl_sub)

        self.pill_dot = Gtk.Label(label="●")
        outer.append(self.pill_dot)
        self.pill_word = Gtk.Label()
        outer.append(self.pill_word)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        outer.append(btns)
        self.btn_start = Gtk.Button(label="Start")
        self.btn_start.set_tooltip_text("Start instance")
        self.btn_start.connect("clicked", self._emit, "start")
        btns.append(self.btn_start)
        self.btn_stop = Gtk.Button(label="Stop")
        self.btn_stop.set_tooltip_text("Stop instance")
        self.btn_stop.connect("clicked", self._emit, "stop")
        btns.append(self.btn_stop)
        self.btn_restart = Gtk.Button(label="Restart")
        self.btn_restart.set_tooltip_text("Restart instance")
        self.btn_restart.connect("clicked", self._emit, "restart")
        btns.append(self.btn_restart)

        self.update_status(status)

    def _emit(self, _btn: Gtk.Button, action: str) -> None:
        if self._on_action is not None:
            self._on_action(action, self.instance_id, None)

    def update_status(self, status: dict) -> None:
        state = (status.get("status") or "stopped").lower()
        name = status.get("name", "?")
        if status.get("password_storage") == "plaintext":
            # H.2 sweep marker: persistent until secured via the detail page.
            self.lbl_name.set_text("⚠ " + name)
            self.lbl_name.set_tooltip_text(
                "Database password stored in plaintext — open details to secure it")
        else:
            self.lbl_name.set_text(name)
            self.lbl_name.set_tooltip_text(None)
        sub = f"{status.get('version', '')}  ·  :{status.get('port', '')}"
        if state == "running" and status.get("pid"):
            extra = f"  ·  pid {status['pid']}"
            if status.get("cpu_percent") is not None:
                extra += f"  ·  {status['cpu_percent']:.1f}%"
            if status.get("memory_mb") is not None:
                extra += f"  ·  {status['memory_mb']:.0f}MB"
            sub += extra
        self.lbl_sub.set_text(sub)

        pill_class = PILL_CLASS.get(state, "status-stopped")
        if pill_class != self._pill_class:
            for cls in PILL_CLASS.values():
                self.pill_dot.remove_css_class(cls)
                self.pill_word.remove_css_class(cls)
            self.pill_dot.add_css_class(pill_class)
            self.pill_word.add_css_class(pill_class)
            self._pill_class = pill_class
        self.pill_word.set_text(state.capitalize())

        running = state == "running"
        is_draft = state == "draft"
        self.btn_start.set_visible(not running and not is_draft)
        self.btn_stop.set_visible(running)
        self.btn_restart.set_visible(running)


class InstanceListPage(Gtk.Box):
    """Sidebar: 'Instances' heading + live list. Polling is owned by MainWindow."""

    def __init__(self, on_action=None, on_select=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self._on_action = on_action
        self._on_select = on_select
        self._rows: dict[str, InstanceRow] = {}

        heading = Gtk.Label(label="Instances", xalign=0)
        heading.add_css_class("heading")
        self.append(heading)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._forward_select)
        scrolled.set_child(self.listbox)

        self.full_refresh()

    def _forward_select(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._on_select is not None and isinstance(row, InstanceRow):
            self._on_select(row.instance_id)

    def full_refresh(self) -> None:
        """Rebuild from the registry (structural refresh)."""
        statuses = [
            {"id": i.id, "name": i.name, "status": i.status, "pid": i.pid,
             "port": i.port, "version": i.version,
             "cpu_percent": None, "memory_mb": None,
             "password_storage": i.password_storage,
             "provisioning_mode": i.provisioning_mode}
            for i in list_instances()
        ]
        self.set_statuses(statuses, allow_structural=True)

    def set_statuses(self, statuses: list[dict], allow_structural: bool = True) -> None:
        """Apply a poll batch: in-place updates, plus add/remove when needed."""
        incoming = {s["id"]: s for s in statuses}
        for gone in [rid for rid in self._rows if rid not in incoming]:
            self.listbox.remove(self._rows.pop(gone))
        for status in statuses:
            row = self._rows.get(status["id"])
            if row is None and allow_structural:
                row = InstanceRow(status, on_action=self._on_action)
                self._rows[status["id"]] = row
                self.listbox.append(row)
            elif row is not None:
                row.update_status(status)
        if not statuses and allow_structural:
            row = self.listbox.get_row_at_index(0)
            if row is None:
                placeholder = Gtk.ListBoxRow()
                placeholder.set_child(Gtk.Label(label="No instances yet"))
                placeholder.set_sensitive(False)
                self.listbox.append(placeholder)
