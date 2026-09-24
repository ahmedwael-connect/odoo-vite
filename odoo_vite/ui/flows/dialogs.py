"""Shared dialog/widget helpers (Sprint R: used by 3+ flow modules)."""

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.ui import HAS_ADW, HAS_ALERT  # noqa: E402

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


    def build_progress_dialog(parent, title: str):
        """Modal log dialog for long module ops. Returns (dialog, append, done)."""
        dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True,
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

