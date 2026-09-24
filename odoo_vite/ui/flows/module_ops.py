"""Module management flows (Sprint R — moved verbatim from MainWindow).

List/install/update/uninstall/diff/deps-graph/scaffold + the shared
confirm-command dialog and record diff helper. `self.win` is the MainWindow.
"""

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from odoo_vite.core.registry import get_instance  # noqa: E402
from odoo_vite.ui import Adw, HAS_ADW, HAS_ALERT  # noqa: E402
from odoo_vite.ui.flows.dialogs import (  # noqa: E402
    _finish_alert,
    build_progress_dialog,
)


class ModuleOpsFlows:
    """See module docstring. Constructed once: ModuleOpsFlows(win)."""

    def __init__(self, win) -> None:
        self.win = win

    def _mod_instance(self, instance_id: str):
        inst = get_instance(instance_id)
        if inst is None:
            self.win.toast("Instance disappeared")
            return None, ""
        db_name = (inst.primary_db or "").strip()
        if not db_name:
            self.win._select_row(instance_id)
            if self.win.detail.instance_id == instance_id:
                self.win.detail.show_error("No primary database set — pick one first.")
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
        if self.win.detail.instance_id == instance_id:
            try:
                self.win.detail.set_modules(modules, diff)
                if error:
                    self.win.detail.show_error(error)
            except Exception:
                pass
        return False


    def _mod_install_flow(self, instance_id: str, names: list) -> None:
        names = [n for n in (names or []) if n]
        if not names:
            self.win.toast("Select installable modules first")
            return
        inst, db_name = self._mod_instance(instance_id)
        if inst is None:
            return
        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {db_name} -i {','.join(names)} --stop-after-init")

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()
            dlg, append, done = build_progress_dialog(self.win, 
                f"Install {', '.join(names)}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.install_modules(
                    inst, db_name, names, progress_cb=_feed)
                GLib.idle_add(done, res.ok, res.message)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
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
            self.win.toast("Select modules to update first")
            return
        inst, db_name = self._mod_instance(instance_id)
        if inst is None:
            return
        cmd = (f"{inst.venv_path}/bin/python {inst.community_path}/odoo-bin "
               f"-c {inst.conf_path} -d {db_name} -u {','.join(names)} --stop-after-init")

        def _go(confirmed: bool) -> None:
            if not confirmed:
                return
            self.win._op_start()
            dlg, append, done = build_progress_dialog(self.win, 
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
                    GLib.idle_add(self.win._show_result, instance_id, False, msg)
                else:
                    GLib.idle_add(done, True, f"Updated {', '.join(names)}")
                    GLib.idle_add(self.win._show_result, instance_id, True,
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
                self.win.toast("No auto-update modules configured — nothing to update")
                return
            self.win._op_start()
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
            self.win._confirm_async("Update code and modules?",
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
                GLib.idle_add(self.win._show_result, instance_id, False,
                              f"Pre-update backup failed, aborting update: {res.message}")
                GLib.idle_add(self.win._op_end)
                return
            GLib.idle_add(self.win.toast, f"Pre-update backup saved: {dest}")
            self._run_update_code(instance_id, inst, mods)

        threading.Thread(target=_work, daemon=True).start()


    def _run_update_code(self, instance_id: str, inst, mods: list) -> None:
        dlg, append, done = build_progress_dialog(self.win, "Update code + modules")
        dlg.present()

        def _feed(line: str) -> None:
            GLib.idle_add(append, line)

        def _work() -> None:
            from odoo_vite.core import module_manager

            res = module_manager.update_code(inst, mods, progress_cb=_feed)
            GLib.idle_add(done, res.ok, res.message)
            GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
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
            self.win._op_start()
            dlg, append, done = build_progress_dialog(self.win, f"Uninstall {name}")
            dlg.present()

            def _feed(line: str) -> None:
                GLib.idle_add(append, line)

            def _work() -> None:
                from odoo_vite.core import module_manager

                res = module_manager.uninstall_modules(
                    inst, db_name, [name], progress_cb=_feed)
                GLib.idle_add(done, res.ok, res.message)
                GLib.idle_add(self.win._show_result, instance_id, res.ok, res.message)
                GLib.idle_add(self._refresh_modules_only, instance_id)

            threading.Thread(target=_work, daemon=True).start()

        self.win._confirm_async(
            f"Uninstall '{name}'?",
            f"Uninstalling can cascade to dependent modules. Odoo itself "
            f"will refuse or report blocking dependents in '{db_name}' — "
            "whatever it reports is shown verbatim.",
            "Uninstall", _go, destructive=True)


    def _mod_deps_dialog(self, instance_id: str) -> None:
        inst = get_instance(instance_id)
        if inst is None:
            return
        self.win._op_start()

        def _work() -> None:
            from odoo_vite.core import module_manager

            db_name = (inst.primary_db or "").strip()
            res = module_manager.get_dependency_graph(inst, db_name) if db_name else None
            GLib.idle_add(self._show_deps_dialog, instance_id,
                           res.data if res and res.ok else None,
                           "" if res and res.ok else (res.message if res else "no DB"))
            GLib.idle_add(self.win._op_end)

        threading.Thread(target=_work, daemon=True).start()


    def _show_deps_dialog(self, instance_id: str, graph: dict | None,
                          error: str) -> bool:
        if graph is None:
            self.win.toast(f"Dependency graph unavailable: {error[:160]}")
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
            self.win.toast(f"{len(nodes)} modules, {len(edges)} edges (no dialog backend)")
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
            self.win._confirm_async(heading, command, confirm_label, _on_ok)


def diff_record_values(current: dict, new: dict) -> dict:
    """Changed fields {key: (old, new)} for the update-preview dialog.

    Compares stringified values (Odoo round-trips most scalars through
    text); relational blobs are skipped upstream, never diffed here.
    """
    current = current or {}
    changed = {}
    for key, value in (new or {}).items():
        if str(current.get(key)) != str(value):
            changed[key] = (current.get(key), value)
    return changed

