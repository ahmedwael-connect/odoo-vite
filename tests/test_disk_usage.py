"""U5.3: disk usage measurement (pure core, tmp trees)."""

from odoo_vite.core.disk_usage import measure_dir, measure_instance
from odoo_vite.core.instance import Instance


def test_measure_dir_sums_files(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b").write_bytes(b"y" * 50)
    assert measure_dir(tmp_path) == 150
    assert measure_dir(tmp_path / "nope") == 0


def test_measure_instance_breakdown(tmp_path):
    base = tmp_path / "inst"
    (base / "community").mkdir(parents=True)
    (base / "community" / "code.py").write_bytes(b"c" * 1000)
    (base / "venv").mkdir()
    (base / "venv" / "py").write_bytes(b"v" * 500)
    (base / "custom_addons").mkdir()
    (base / "logs").mkdir()
    inst = Instance(
        name="D", path=str(base), community_path=str(base / "community"),
        venv_path=str(base / "venv"),
        custom_addons_path=str(base / "custom_addons"))
    res = measure_instance(inst)
    assert res.ok, res.message
    assert res.data["total_bytes"] >= 1500
    assert res.data["parts"]["code"] == 1000
    assert res.data["parts"]["venv"] == 500
    assert res.data["human"]


def test_measure_instance_missing_path():
    inst = Instance(name="Ghost", path="/no-such-dir-xyz")
    assert not measure_instance(inst).ok
