"""3.2.0 F2: addon-path type detection, auto-typing, and regenerate parity."""

from pathlib import Path

from odoo_vite.core import addon_paths, conf_writer, devwatch
from odoo_vite.core.instance import Instance
from odoo_vite.core.path_types import PATH_TYPES, detect_path_type, detect_types


def _tree(root: Path, *parts: str) -> Path:
    target = root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _module(root: Path, name: str) -> Path:
    mod = _tree(root, name)
    (mod / "__manifest__.py").write_text("{'version': '17.0'}")
    return mod


def _instance(tmp_path, **overrides):
    base = tmp_path / "inst"
    kwargs = {
        "name": "F2",
        "version": "17.0",
        "path": str(base),
        "community_path": str(base / "community"),
        "conf_path": str(base / "odoo.conf"),
    }
    kwargs.update(overrides)
    return Instance(**kwargs)


# ------------------------------------------------------------- detection
def test_detect_community_paths(tmp_path):
    inst = _instance(tmp_path)
    _tree(tmp_path, "inst", "community")
    assert detect_path_type(inst, inst.community_path) == "community"
    assert detect_path_type(inst, str(Path(inst.community_path) / "addons")) == "community"
    assert detect_path_type(inst, str(Path(inst.community_path) / "odoo" / "addons")) == "community"
    # odoo-bin marks the checkout root even when community_path disagrees
    other = _tree(tmp_path, "checkout")
    (other / "odoo-bin").touch()
    assert detect_path_type(inst, str(other)) == "community"


def test_detect_enterprise_paths(tmp_path):
    inst = _instance(tmp_path, enterprise_path=str(tmp_path / "ent"))
    _tree(tmp_path, "ent")
    assert detect_path_type(inst, inst.enterprise_path) == "enterprise"
    # folder shipping web_enterprise, wired elsewhere
    wired = _tree(tmp_path, "somewhere")
    _module(wired, "web_enterprise")
    assert detect_path_type(inst, str(wired)) == "enterprise"


def test_detect_custom_paths(tmp_path):
    base = tmp_path / "inst"
    custom = _tree(base, "custom_addons")
    inst = _instance(tmp_path, custom_addons_path=str(custom))
    assert detect_path_type(inst, str(custom)) == "custom"
    # comma-separated custom list
    other = _tree(tmp_path, "more_custom")
    inst2 = _instance(tmp_path, custom_addons_path=f"{custom}, {other}")
    assert detect_path_type(inst2, str(other)) == "custom"


def test_detect_extra_and_unknown(tmp_path):
    inst = _instance(tmp_path)
    extra = _tree(tmp_path, "shared_addons")
    _module(extra, "my_addon")
    assert detect_path_type(inst, str(extra)) == "extra"
    plain = _tree(tmp_path, "not_addons")
    assert detect_path_type(inst, str(plain)) == "unknown"
    assert detect_path_type(inst, "") == "unknown"
    assert detect_path_type(inst, str(tmp_path / "gone")) == "unknown"
    assert set(PATH_TYPES) >= {"community", "enterprise", "custom", "extra", "unknown"}
    # detection never raises even on a weird instance
    assert detect_path_type(Instance(name="bare"), "/tmp") in PATH_TYPES


def test_detect_types_copies_rows(tmp_path):
    inst = _instance(tmp_path)
    extra = _tree(tmp_path, "extra")
    _module(extra, "m")
    rows = detect_types(inst, [{"path": str(extra), "enabled": True},
                               "junk-entry"])
    assert rows == [{"path": str(extra), "enabled": True, "type": "extra"}]


# ------------------------------------------------- state read/write/auto
def test_get_addons_state_keeps_and_fills_types(tmp_path):
    base = tmp_path / "inst"
    community_addons = _tree(base, "community", "addons")
    _module(community_addons, "base")
    custom = _tree(base, "custom_addons")
    _module(custom, "mine")
    inst = _instance(tmp_path, addons_state=[
        {"path": str(community_addons), "enabled": True, "type": "community"},
        # legacy row without type → classified at read time
        {"path": str(custom), "enabled": True},
    ])
    state = addon_paths.get_addons_state(inst)
    assert [e["type"] for e in state] == ["community", "custom"]


def test_get_addons_state_migrating_from_conf_carries_type(tmp_path):
    base = tmp_path / "inst"
    community_addons = _tree(base, "community", "addons")
    _module(community_addons, "base")
    conf = base / "odoo.conf"
    conf.write_text(f"[options]\naddons_path = {community_addons}\n")
    inst = _instance(tmp_path)
    migrated = addon_paths.get_addons_state(inst)
    assert migrated == [{"path": str(community_addons), "enabled": True,
                         "type": "community"}]


def test_apply_addons_state_detects_missing_type(tmp_path, monkeypatch):
    db = tmp_path / "reg.db"
    monkeypatch.setenv("ODOO_VITE_DB", str(db))
    from odoo_vite.core.registry import create_instance, get_instance

    base = tmp_path / "inst"
    custom = _tree(base, "custom_addons")
    _module(custom, "mine")
    base.mkdir(parents=True, exist_ok=True)
    (base / "odoo.conf").write_text("[options]\naddons_path = /keep\n")
    inst = _instance(tmp_path, custom_addons_path=str(custom))
    assert create_instance(inst, db).ok
    # enterprise-load / marketplace-import style: plain dict, no type
    res = addon_paths.apply_addons_state(
        inst.id, [{"path": "/keep", "enabled": True},
                  {"path": str(custom), "enabled": True}], db_path=db)
    assert res.ok, res.message
    stored = get_instance(inst.id, db).addons_state
    assert stored[0]["type"] == "unknown"  # /keep does not exist
    assert stored[1]["type"] == "custom"


def test_auto_type_addons_registry_only(tmp_path, monkeypatch):
    db = tmp_path / "reg.db"
    monkeypatch.setenv("ODOO_VITE_DB", str(db))
    from odoo_vite.core.registry import create_instance, get_instance

    base = tmp_path / "inst"
    base.mkdir(parents=True)
    conf = base / "odoo.conf"
    conf.write_text("[options]\naddons_path = /unused\n")
    community_addons = _tree(base, "community", "addons")
    _module(community_addons, "base")
    inst = _instance(tmp_path, addons_state=[
        {"path": str(community_addons), "enabled": True},  # legacy: no type
        {"path": str(base / "custom_addons"), "enabled": False},
    ])
    assert create_instance(inst, db).ok
    before = conf.read_text()

    res = addon_paths.auto_type_addons(inst.id, db_path=db)
    assert res.ok, res.message
    entries = res.data["entries"]
    assert [e["type"] for e in entries] == ["community", "custom"]
    stored = get_instance(inst.id, db).addons_state
    assert [e["type"] for e in stored] == ["community", "custom"]
    assert [e["enabled"] for e in stored] == [True, False]
    # registry-only: the conf file is untouched
    assert conf.read_text() == before

    assert not addon_paths.auto_type_addons("nope", db_path=db).ok
    empty = _instance(tmp_path, name="empty", conf_path="")
    assert create_instance(empty, db).ok
    res = addon_paths.auto_type_addons(empty.id, db_path=db)
    assert res.ok and res.data["entries"] == []


# ------------------------------------------------------ regenerate parity
def test_write_conf_follows_addons_state(tmp_path):
    """3.2.0: regenerate must use the structured list — extra paths kept,
    disabled entries dropped — instead of rebuilding from instance fields."""
    base = tmp_path / "inst"
    community_addons = _tree(base, "community", "addons")
    extra = _tree(tmp_path, "shared")
    custom = _tree(base, "custom_addons")
    for tree, name in ((community_addons, "base"), (extra, "shared_addon"),
                       (custom, "mine")):
        _module(tree, name)
    inst = _instance(
        tmp_path,
        custom_addons_path=str(custom),
        addons_state=[
            {"path": str(community_addons), "enabled": True, "type": "community"},
            {"path": str(extra), "enabled": True, "type": "extra"},
            {"path": str(custom), "enabled": False, "type": "custom"},
        ],
    )
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message
    assert res.data["addons_path"] == f"{community_addons},{extra}"
    text = Path(res.data["conf_path"]).read_text()
    assert str(extra) in text
    assert str(custom) not in text  # disabled entry stays out


def test_write_conf_falls_back_to_fields_without_state(tmp_path):
    inst = _instance(tmp_path)
    res = conf_writer.write_conf(inst)
    assert res.ok, res.message
    addons = res.data["addons_path"].split(",")
    assert addons[0].endswith("community/addons")
    assert addons[-1].endswith("custom_addons")


def test_watch_roots_uses_enabled_state(tmp_path):
    base = tmp_path / "inst"
    community_addons = _tree(base, "community", "addons")
    extra = _tree(tmp_path, "shared")
    disabled = _tree(base, "custom_addons")
    inst = _instance(
        tmp_path,
        custom_addons_path=str(disabled),
        addons_state=[
            {"path": str(community_addons), "enabled": True},
            {"path": str(extra), "enabled": True},
            {"path": str(disabled), "enabled": False},
        ],
    )
    roots = devwatch.watch_roots(inst)
    assert str(extra) in roots
    assert str(community_addons) in roots
    assert str(disabled) not in roots  # disabled: not loaded by Odoo
    assert inst.community_path in roots  # repo root (git pull parity)


def test_watch_roots_legacy_fallback(tmp_path):
    custom = _tree(tmp_path, "custom")
    inst = _instance(tmp_path, custom_addons_path=str(custom))
    roots = devwatch.watch_roots(inst)
    # no structured state yet: the legacy field-based scan applies and the
    # (nonexistent) community root is filtered out by is_dir()
    assert roots == [str(custom)]
