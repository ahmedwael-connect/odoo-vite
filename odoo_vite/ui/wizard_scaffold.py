"""Scaffold wizard (Sprint 6, Ticket B.7): form → review → generate.

Pure code generation into the instance's custom_addons folder. Two steps:
1. Module definition form (meta + models/fields as structured lines +
   feature toggles). 2. Review + Generate.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402

    HAS_ADW = True
    HAS_NAV_VIEW = hasattr(Adw, "NavigationView")
except (ImportError, ValueError):
    Adw = None  # type: ignore
    HAS_ADW = False
    HAS_NAV_VIEW = False

from odoo_vite.core import module_scaffolder as scaffolder  # noqa: E402

VERSIONS = ["15.0", "16.0", "17.0", "18.0", "19.0"]


class ScaffoldWizard(Gtk.Window):
    def __init__(self, parent: Gtk.Window | None = None,
                 custom_addons: str = "", odoo_version: str = "17.0") -> None:
        super().__init__(title="New Module — Step 1 of 2")
        self.set_modal(True)
        self.set_default_size(640, 560)
        if parent is not None:
            self.set_transient_for(parent)
        self._custom_addons = custom_addons or ""
        self._generating = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        header = Adw.HeaderBar() if HAS_ADW else Gtk.HeaderBar()
        self.set_titlebar(header)

        self.btn_back = Gtk.Button(label="‹ Back")
        self.btn_back.connect("clicked", lambda _b: self._show_step(1))
        self.btn_next = Gtk.Button(label="Next ›")
        self.btn_next.add_css_class("suggested-action")
        self.btn_next.connect("clicked", lambda _b: self._on_next())
        self.btn_close = Gtk.Button(label="Close")
        self.btn_close.connect("clicked", lambda _b: self.close())
        if hasattr(header, "pack_start"):
            header.pack_start(self.btn_back)
            header.pack_start(self.btn_close)
        if hasattr(header, "pack_end"):
            header.pack_end(self.btn_next)

        self.page1 = self._build_form_page(odoo_version)
        self.page2 = self._build_review_page()
        if HAS_ADW and HAS_NAV_VIEW:
            self._nav = Adw.NavigationView()
            self._nav_p1 = Adw.NavigationPage(child=self.page1, title="Definition")
            self._nav_p2 = Adw.NavigationPage(child=self.page2, title="Review")
            self._nav.push(self._nav_p1)
            self._use_nav = True
            outer.append(self._nav)
        else:
            self._use_nav = False
            self._stack = Gtk.Stack()
            self._stack.add_named(self.page1, "form")
            self._stack.add_named(self.page2, "review")
            outer.append(self._stack)
        self._show_step(1)

    # ------------------------------------------------------------------ nav
    def _show_step(self, step: int) -> None:
        self._step = step
        if self._use_nav:
            if step == 1:
                self._nav.pop_to_page(self._nav_p1)
            elif self._nav.get_visible_page() is not self._nav_p2:
                self._nav.push(self._nav_p2)
        else:
            self._stack.set_visible_child_name("form" if step == 1 else "review")
        self.set_title(f"New Module — Step {step} of 2")
        self.btn_back.set_visible(step == 2)
        self.btn_close.set_visible(step == 1)
        self.btn_next.set_visible(step == 1)
        if step == 2:
            self._fill_review()

    def _on_next(self) -> None:
        if self._step == 1 and self._collect_definition()[1] is None:
            self._show_step(2)

    def _refresh_parent(self) -> None:
        parent = self.get_transient_for()
        if parent is not None and hasattr(parent, "toast"):
            try:
                parent.toast("Module scaffolded — update the Apps list in Odoo to see it")
            except Exception:
                pass

    # ------------------------------------------------------------------ form
    def _build_form_page(self, odoo_version: str = "17.0") -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        def _row(caption: str, widget: Gtk.Widget):
            box.append(Gtk.Label(label=caption, xalign=0))
            box.append(widget)
            return widget

        self.entry_tech = Gtk.Entry(placeholder_text="my_library")
        self.entry_tech.connect("changed", lambda _e: self._validate())
        _row("Technical name (required)", self.entry_tech)
        self.err_tech = Gtk.Label(xalign=0)
        self.err_tech.add_css_class("error")
        box.append(self.err_tech)

        self.entry_pretty = Gtk.Entry(placeholder_text="My Library")
        _row("Display name (optional)", self.entry_pretty)

        self.drop_version = Gtk.DropDown(model=Gtk.StringList.new(VERSIONS))
        try:
            self.drop_version.set_selected(VERSIONS.index(odoo_version))
        except ValueError:
            self.drop_version.set_selected(2)
        box.append(Gtk.Label(label="Odoo version", xalign=0))
        box.append(self.drop_version)

        self.entry_summary = Gtk.Entry(placeholder_text="One-line summary")
        _row("Summary", self.entry_summary)

        toggles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.toggles = {}
        for key, label, default in (("views", "Views", True),
                                    ("menus", "Menus", True),
                                    ("security", "Security", True),
                                    ("controllers", "Controllers", False),
                                    ("tests", "Tests", False),
                                    ("demo", "Demo data", False)):
            chk = Gtk.CheckButton(label=label, active=default)
            self.toggles[key] = chk
            toggles.append(chk)
        box.append(Gtk.Label(label="Features", xalign=0))
        box.append(toggles)

        box.append(Gtk.Label(
            label="Models — one per line:  library.book: Book", xalign=0))
        self.text_models = Gtk.TextView(monospace=True)
        self.text_models.get_buffer().connect("changed", lambda *_a: self._validate())
        box.append(self.text_models)

        box.append(Gtk.Label(
            label="Fields — one per line:  library.book.name: char:required  ·  "
                  "library.book.author_id: many2one:res.partner  ·  "
                  "library.book.state: selection:draft=Draft,done=Done", xalign=0,
            wrap=True))
        self.text_fields = Gtk.TextView(monospace=True)
        self.text_fields.get_buffer().connect("changed", lambda *_a: self._validate())
        box.append(self.text_fields)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(box)
        return scrolled

    def _parse_form(self):
        """Returns (definition, error). Shared by validation + review + generate."""

        tech = self.entry_tech.get_text().strip()
        ver_item = self.drop_version.get_selected_item()
        version = ver_item.get_string() if ver_item is not None else "17.0"
        models = []
        for line in self.text_models.get_buffer().get_text(
                self.text_models.get_buffer().get_start_iter(),
                self.text_models.get_buffer().get_end_iter(), True).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                return None, f"bad model line (want 'name: Label'): {line}"
            name, _, label = line.partition(":")
            models.append({"name": name.strip(), "description": label.strip(),
                           "_fields": []})
        by_model = {m["name"]: m for m in models}
        for line in self.text_fields.get_buffer().get_text(
                self.text_fields.get_buffer().get_start_iter(),
                self.text_fields.get_buffer().get_end_iter(), True).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            left, _, rest = line.partition(":")
            left, rest = left.strip(), (rest or "").strip()
            if "." not in left or not rest:
                return None, f"bad field line: {line}"
            model_name, _, field_name = left.rpartition(".")
            if model_name not in by_model:
                return None, f"field for unknown model '{model_name}': {line}"
            chunks = [c.strip() for c in rest.split(":") if c.strip()]
            ftype = chunks[0] if chunks else "char"
            extra = chunks[1:]
            field = {"name": field_name, "type": ftype}
            for item in extra:
                if item == "required":
                    field["required"] = True
                elif ftype == "many2one" and "relation" not in field:
                    field["relation"] = item
                elif ftype == "selection" and "selection" not in field:
                    pairs = []
                    for pair in item.split(","):
                        if "=" in pair:
                            k, _, v = pair.partition("=")
                            pairs.append([k.strip(), v.strip()])
                    field["selection"] = pairs
            by_model[model_name]["_fields"].append(field)
        definition = {
            "technical_name": tech,
            "pretty_name": self.entry_pretty.get_text().strip(),
            "odoo_version": version,
            "summary": self.entry_summary.get_text().strip(),
            "models": [{"name": m["name"], "description": m["description"],
                        "fields": m["_fields"]} for m in models],
        }
        for key, chk in self.toggles.items():
            definition[key] = chk.get_active()
        return definition, None

    def _collect_definition(self):
        definition, error = self._parse_form()
        if error is not None:
            return None, error
        # Field-level validation lives here; full semantic validation runs
        # in the scaffolder at Generate time (errors surface on the page).
        if not definition["technical_name"]:
            return None, "Technical name is required."
        return definition, None

    def _validate(self) -> None:
        _, error = self._collect_definition()
        self.err_tech.set_text(error or "")
        self.btn_next.set_sensitive(error is None)

    # ---------------------------------------------------------------- review
    def _build_review_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.append(Gtk.Label(label="Review & generate", xalign=0))
        self.review_list = Gtk.ListBox()
        self.review_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self.review_list)
        self.btn_generate = Gtk.Button(label="Generate into custom_addons")
        self.btn_generate.add_css_class("suggested-action")
        self.btn_generate.connect("clicked", self._on_generate)
        box.append(self.btn_generate)
        self.lbl_gen_error = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.lbl_gen_error.add_css_class("error")
        box.append(self.lbl_gen_error)
        self.spinner = Gtk.Spinner()
        box.append(self.spinner)
        return box

    def _fill_review(self) -> None:
        while True:
            row = self.review_list.get_row_at_index(0)
            if row is None:
                break
            self.review_list.remove(row)
        definition, _ = self._collect_definition()
        definition = definition or {}
        for caption, value in [
                ("Module", definition.get("technical_name", "")),
                ("Odoo version", definition.get("odoo_version", "")),
                ("Models", str(len(definition.get("models", [])))),
                ("Destination", (self._custom_addons + "/" +
                                 definition.get("technical_name", ""))) ]:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.append(Gtk.Label(label=caption, xalign=0, hexpand=True))
            hbox.append(Gtk.Label(label=value, xalign=1))
            row.set_child(hbox)
            self.review_list.append(row)

    def _on_generate(self, _btn: Gtk.Button) -> None:
        if self._generating:
            return
        definition, error = self._collect_definition()
        if error is not None or definition is None:
            self.lbl_gen_error.set_text(error or "Invalid definition.")
            return
        if not self._custom_addons:
            self.lbl_gen_error.set_text(
                "Instance has no custom_addons folder recorded.")
            return
        self._generating = True
        self.btn_generate.set_sensitive(False)
        self.spinner.start()

        def _work() -> None:
            res = scaffolder.scaffold(definition, self._custom_addons)
            GLib.idle_add(self._on_generated, res.ok, res.message)

        import threading

        threading.Thread(target=_work, daemon=True).start()

    def _on_generated(self, ok: bool, message: str) -> bool:
        self._generating = False
        self.spinner.stop()
        if ok:
            self._refresh_parent()
            self.close()
        else:
            self.lbl_gen_error.set_text(message)
            self.btn_generate.set_sensitive(True)
        return False

    def _refresh_parent(self) -> None:
        parent = self.get_transient_for()
        if parent is not None and hasattr(parent, "toast"):
            try:
                parent.toast("Module scaffolded — update the Apps list in Odoo to see it")
            except Exception:
                pass
