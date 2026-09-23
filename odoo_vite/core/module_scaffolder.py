"""Odoo module scaffolding (Sprint 6, Ticket B.7).

Pure code generation from a declarative definition — touches no live
instance or database. Output drops straight into an instance's
custom_addons folder so it's immediately visible to that instance.

Definition shape (structured form, deliberately not a DSL):
{
  "technical_name": "my_library",      # required, ^[a-z_][a-z0-9_]*$
  "pretty_name": "My Library",         # default: prettified technical name
  "odoo_version": "17.0",              # default "17.0"
  "summary": "...", "author": "...", "website": "...", "license": "LGPL-3",
  "application": True,
  "models": [                          # at least one recommended, not required
    {"name": "library.book",           # dotted model name, required
     "description": "Book",            # user-facing label
     "fields": [                       # field dicts:
       {"name": "name", "type": "char", "required": True},
       {"name": "pages", "type": "integer"},
       {"name": "author_id", "type": "many2one", "relation": "res.partner"},
       {"name": "tag", "type": "selection",
        "selection": [["a", "A"], ["b", "B"]]},
     ]},
  ],
  "views": True, "menus": True, "security": True,
  "controllers": False, "tests": False, "demo": False,
}

Supported field types: char, text, integer, float, boolean, date, datetime,
many2one (needs relation), selection (needs selection options), html,
binary. Anything else is rejected loudly (fail fast, don't emit garbage).

No GTK imports.
"""

from __future__ import annotations

import re
from pathlib import Path

from odoo_vite.core.result import Result

TECHNICAL_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
MODEL_RE = re.compile(r"^[a-z_][a-zA-Z0-9_.]*$")

FIELD_TYPES = {
    "char": {"odoo": "Char", "args": []},
    "text": {"odoo": "Text", "args": []},
    "integer": {"odoo": "Integer", "args": []},
    "float": {"odoo": "Float", "args": []},
    "boolean": {"odoo": "Boolean", "args": []},
    "date": {"odoo": "Date", "args": []},
    "datetime": {"odoo": "Datetime", "args": []},
    "html": {"odoo": "Html", "args": []},
    "binary": {"odoo": "Binary", "args": []},
    "many2one": {"odoo": "Many2one", "args": ["relation"]},
    "selection": {"odoo": "Selection", "args": ["selection"]},
}


def _pretty(technical: str) -> str:
    return " ".join(w.capitalize() for w in technical.split("_"))


def _validate(definition: dict) -> tuple[dict, str | None]:
    """Normalize + validate. Returns (normalized, error_or_None)."""
    if not isinstance(definition, dict):
        return {}, "definition must be a dict"
    tech = str(definition.get("technical_name", "")).strip()
    if not TECHNICAL_RE.match(tech):
        return {}, (f"invalid technical_name '{tech}' "
                     "(lowercase letters, digits, _; must not start with a digit)")
    norm: dict = {
        "technical_name": tech,
        "pretty_name": str(definition.get("pretty_name") or _pretty(tech)),
        "odoo_version": str(definition.get("odoo_version") or "17.0"),
        "summary": str(definition.get("summary") or ""),
        "author": str(definition.get("author") or "Odoo Vite"),
        "website": str(definition.get("website") or ""),
        "license": str(definition.get("license") or "LGPL-3"),
        "application": bool(definition.get("application", True)),
        "views": bool(definition.get("views", True)),
        "menus": bool(definition.get("menus", True)),
        "security": bool(definition.get("security", True)),
        "controllers": bool(definition.get("controllers", False)),
        "tests": bool(definition.get("tests", False)),
        "demo": bool(definition.get("demo", False)),
        "models": [],
    }
    if not re.fullmatch(r"\d+\.\d+", norm["odoo_version"]):
        return {}, f"invalid odoo_version '{norm['odoo_version']}' (expected e.g. '17.0')"
    models = definition.get("models") or []
    if not isinstance(models, list):
        return {}, "models must be a list"
    for idx, model in enumerate(models):
        if not isinstance(model, dict):
            return {}, f"models[{idx}] must be a dict"
        name = str(model.get("name", "")).strip()
        if not MODEL_RE.match(name) or "." not in name:
            return {}, (f"models[{idx}].name '{name}' must be a dotted "
                         "model name like 'library.book'")
        fields = []
        for fidx, field in enumerate(model.get("fields") or []):
            if not isinstance(field, dict):
                return {}, f"models[{idx}].fields[{fidx}] must be a dict"
            fname = str(field.get("name", "")).strip()
            if not TECHNICAL_RE.match(fname):
                return {}, f"invalid field name '{fname}'"
            ftype = str(field.get("type", "char")).strip()
            if ftype not in FIELD_TYPES:
                return {}, (f"unsupported field type '{ftype}' "
                             f"(supported: {sorted(FIELD_TYPES)})")
            for arg in FIELD_TYPES[ftype]["args"]:
                if not field.get(arg):
                    return {}, (f"field '{fname}' of type '{ftype}' "
                                 f"needs '{arg}'")
            fields.append({"name": fname, "type": ftype,
                           "required": bool(field.get("required", False)),
                           "relation": str(field.get("relation", "")),
                           "selection": list(field.get("selection", []))})
        models[idx] = {"name": name,
                       "description": str(model.get("description")
                                          or _pretty(name.split(".")[-1])),
                       "fields": fields}
    norm["models"] = models
    return norm, None


def _field_line(field: dict) -> str:
    odoo = FIELD_TYPES[field["type"]]["odoo"]
    args = []
    if field["type"] == "many2one":
        args.append(f"comodel_name='{field['relation']}'")
    elif field["type"] == "selection":
        args.append(f"selection={field['selection']!r}")
    if field["required"]:
        args.append("required=True")
    suffix = f", {', '.join(args)}" if args else ""
    return f"    {field['name']} = fields.{odoo}(string='{field['name'].replace('_', ' ').title()}'{suffix})"


def _class_name(model_name: str) -> str:
    return "".join(p.capitalize() for p in model_name.replace(".", "_").split("_"))


def scaffold(definition: dict, dest_dir: str | Path) -> Result:
    """Generate the module folder. Refuses to overwrite an existing module."""
    norm, error = _validate(definition)
    if error is not None:
        return Result.failure(error)
    tech = norm["technical_name"]
    root = Path(dest_dir).expanduser() / tech
    if root.exists():
        return Result.failure(
            f"Refusing to overwrite existing folder {root} — pick another "
            "name or delete it first")
    try:
        files: dict[str, str] = {}
        files["__init__.py"] = "from . import models\n" + (
            "from . import controllers\n" if norm["controllers"] else "")
        models_init = []
        for model in norm["models"]:
            cls = _class_name(model["name"])
            lines = [f"from odoo import fields, models\n\n\nclass {cls}(models.Model):",
                     f'    _name = {model["name"]!r}',
                     f'    _description = {model["description"]!r}\n']
            for field in model["fields"]:
                lines.append(_field_line(field))
            files[f"models/{model['name'].split('.')[-1]}.py"] = "\n".join(lines) + "\n"
            models_init.append(f"from . import {model['name'].split('.')[-1]}")
        files["models/__init__.py"] = "\n".join(models_init) + ("\n" if models_init else "")

        data_files, demo_files = [], []
        if norm["security"] and norm["models"]:
            access = ["id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink"]
            for model in norm["models"]:
                short = model["name"].replace(".", "_")
                access.append(
                    f"access_{short}_user,{model['description']},"
                    f"model_{short},base.group_user,1,1,1,1")
            files["security/ir.model.access.csv"] = "\n".join(access) + "\n"
            data_files.append("security/ir.model.access.csv")
        if norm["views"] and norm["models"]:
            for model in norm["models"]:
                short = model["name"].replace(".", "_")
                view = model["name"].split(".")[-1]
                xml = [f'<?xml version="1.0" encoding="utf-8"?>', "<odoo>",
                       f'    <record id="view_{view}_tree" model="ir.ui.view">',
                       '        <field name="name">' + view + '.tree</field>',
                       f'        <field name="model">{model["name"]}</field>',
                       '        <field name="arch" type="xml">',
                       '            <tree>',
                       "".join(f"\n                        <field name=\"{f['name']}\"/>"
                               for f in model["fields"][:8]),
                       '            </tree>', '        </field>', '    </record>',
                       f'    <record id="view_{view}_form" model="ir.ui.view">',
                       '        <field name="name">' + view + '.form</field>',
                       f'        <field name="model">{model["name"]}</field>',
                       '        <field name="arch" type="xml">',
                       '            <form>',
                       '                <sheet>',
                       '                    <group>',
                       "".join(f"\n                        <field name=\"{f['name']}\"/>"
                               for f in model["fields"]),
                       '                    </group>',
                       '                </sheet>', '            </form>',
                       '        </field>', '    </record>']
                if norm["menus"]:
                    xml += [f'    <record id="action_{view}" model="ir.actions.act_window">',
                            f'        <field name="name">{model["description"]}</field>',
                            f'        <field name="res_model">{model["name"]}</field>',
                            '        <field name="view_mode">tree,form</field>',
                            '    </record>',
                            f'    <menuitem id="menu_{view}" name="{model["description"]}"',
                            f'              action="action_{view}"/>']
                xml.append("</odoo>")
                files[f"views/{view}_views.xml"] = "\n".join(xml) + "\n"
                data_files.append(f"views/{view}_views.xml")
        if norm["controllers"]:
            files["controllers/__init__.py"] = "from . import main\n"
            files["controllers/main.py"] = (
                "from odoo import http\n\n\n"
                f"class { _class_name(tech) }Controller(http.Controller):\n"
                f"    @http.route('/{tech}/ping', auth='public')\n"
                "    def ping(self, **kw):\n"
                "        return 'pong'\n")
        if norm["tests"]:
            files["tests/__init__.py"] = "from . import test_basic\n"
            files["tests/test_basic.py"] = (
                "from odoo.tests import TransactionCase\n\n\n"
                "class TestBasic(TransactionCase):\n"
                "    def test_module_installs(self):\n"
                "        self.assertTrue(True)\n")
        if norm["demo"]:
            files["demo/demo.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n'
                                      '    <!-- demo data goes here -->\n</odoo>\n')
            demo_files.append("demo/demo.xml")

        manifest = [
            "{",
            f"    'name': {norm['pretty_name']!r},",
            f"    'version': {norm['odoo_version'] + '.1.0.0'!r},",
            f"    'summary': {norm['summary']!r},",
            f"    'author': {norm['author']!r},",
            f"    'website': {norm['website']!r},",
            f"    'license': {norm['license']!r},",
            "    'depends': ['base'],",
            f"    'data': {data_files!r},",
            f"    'demo': {demo_files!r},",
            f"    'installable': True,",
            f"    'application': {norm['application']!r},",
            "}",
            ""]
        files["__manifest__.py"] = "\n".join(manifest)

        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return Result.failure(f"Cannot write module files: {exc}")
    return Result.success(
        data={"path": str(root), "files": sorted(files)},
        message=f"Module '{tech}' scaffolded at {root} ({len(files)} files)")
