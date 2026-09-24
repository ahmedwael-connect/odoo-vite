"""REG.1 regression: dialog parents must be the window, never the Flows object.

Root cause: the R.5 mechanical move changed `self.X` attribute access to
`self.win.X`, but bare `self` passed as a dialog parent
(`dlg.choose(self, ...)`) was left untouched. PyGObject then fails the call
(no visible dialog — "button does nothing"). GTK-free fakes throughout:
real Gtk widget construction segfaults without a display.
"""

import types

import odoo_vite.core.addon_paths as addon_paths
from odoo_vite.ui.flows import configuration as conf_mod
from odoo_vite.ui.flows.configuration import ConfigurationFlows


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self._children = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name == "get_row_at_index":
            return lambda *a, **k: None
        if name == "get_active":
            return lambda *a, **k: False
        return lambda *a, **k: None

    def append(self, child):
        self._children.append(child)

    def remove(self, child):
        pass

    def __iter__(self):
        return iter([])

    def connect(self, *a, **k):
        return 0


class FakeGtk:
    class Orientation:
        HORIZONTAL = 0
        VERTICAL = 1

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return FakeWidget


class FakeAlertDialog:
    instances = []

    def __init__(self, *args, **kwargs):
        self.chosen_parent = None
        FakeAlertDialog.instances.append(self)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None

    def choose(self, parent, cancellable, callback):
        self.chosen_parent = parent


class FakeAdw:
    AlertDialog = FakeAlertDialog

    class ResponseAppearance:
        SUGGESTED = 1
        DESTRUCTIVE = 2


class FakePango:
    class EllipsizeMode:
        MIDDLE = 1


class FakeWin:
    def __init__(self):
        self.toasts = []
        self.selected = []

    def toast(self, message):
        self.toasts.append(message)

    def _select_row(self, instance_id):
        self.selected.append(instance_id)


def _patch_dialog_env(monkeypatch):
    monkeypatch.setattr(conf_mod, "Gtk", FakeGtk())
    monkeypatch.setattr(conf_mod, "Adw", FakeAdw())
    monkeypatch.setattr(conf_mod, "Pango", FakePango())
    monkeypatch.setattr(conf_mod, "HAS_ADW", True)
    monkeypatch.setattr(conf_mod, "HAS_ALERT", True)
    monkeypatch.setattr(
        conf_mod, "get_instance",
        lambda iid: types.SimpleNamespace(name="T1"))
    monkeypatch.setattr(addon_paths, "get_addons_state", lambda inst: [])
    FakeAlertDialog.instances.clear()


def test_addons_manage_dialog_parents_on_window(monkeypatch):
    """Clicking Manage… must open the dialog parented on the window."""
    _patch_dialog_env(monkeypatch)
    win = FakeWin()
    ConfigurationFlows(win)._addons_manage_dialog("some-id")
    assert FakeAlertDialog.instances, "expected the manager dialog to open"
    parent = FakeAlertDialog.instances[-1].chosen_parent
    assert parent is win, (
        f"dialog parent must be the window, got {type(parent).__name__!r} — "
        "REG.1: bare `self` (the Flows object) breaks Adw.choose() silently")
    assert win.selected == ["some-id"]


def test_addons_manage_dialog_missing_instance_opens_nothing(monkeypatch):
    """Unknown instance id: no dialog, no crash (silent early return)."""
    _patch_dialog_env(monkeypatch)
    monkeypatch.setattr(conf_mod, "get_instance", lambda iid: None)
    win = FakeWin()
    ConfigurationFlows(win)._addons_manage_dialog("nope")
    assert not FakeAlertDialog.instances
