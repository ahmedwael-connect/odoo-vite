"""PSS-8: transfer ops + preferences (no PG, no archives on disk).

Framework-free by construction (imports core + ops.transfer only):
per docs/slint-thread-safety.md, worker threads and Slint values never
coexist in tests. Bundle permission 600 is core-tested
(tests/test_transfer.py); here we pin the Slint contracts: guards,
filenames, prefs round-trip, preview failures.
"""

import asyncio
import json
import tarfile



from odoo_vite.ops.transfer import (  # noqa: E402
    TransferOps,
    bundle_filename,
    read_preferences,
    save_preferences,
)


def _ops():
    messages, refreshes = [], []
    ops = TransferOps(lambda m, k="info": messages.append((m, k)),
                      lambda: refreshes.append(1))
    return ops, messages, refreshes


def test_bundle_filename():
    assert bundle_filename("Client A!", "20240101-000000") == \
        "Client_A__20240101-000000.tar.gz"
    assert bundle_filename("", "s") == "instance_s.tar.gz"


def test_preferences_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "prefs.db"))
    prefs = read_preferences()
    assert prefs["mode"] in ("developer", "managed")
    assert "keyring" in prefs["keyring_text"].lower()
    assert isinstance(prefs["db_path"], str)
    res = save_preferences("managed")
    assert res.ok
    assert read_preferences()["mode"] == "managed"
    res = save_preferences("developer")
    assert res.ok
    res = save_preferences("bogus")
    assert not res.ok


def test_preview_rejects_bogus(tmp_path):
    ops, _, _ = _ops()
    res = ops.preview(str(tmp_path / "nope.tar.gz"))
    assert not res.ok
    bad = tmp_path / "bad.tar.gz"
    bad.write_text("not a tarball")
    assert not ops.preview(str(bad)).ok


def _bundle(tmp_path, name="Bundled", version="17.0", port=8071):
    archive = tmp_path / "bundle.tar.gz"
    manifest = {"format": 1, "name": name, "version": version,
                "port": port}
    with tarfile.open(archive, "w:gz") as tar:
        info_file = tmp_path / "instance.json"
        info_file.write_text(json.dumps(manifest))
        tar.add(str(info_file), arcname="instance.json")
    return str(archive)


def test_preview_reads_manifest(tmp_path):
    ops, _, _ = _ops()
    res = ops.preview(_bundle(tmp_path))
    assert res.ok
    assert res.data["name"] == "Bundled"
    assert res.data["port"] == 8071


def test_unknown_ids_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "t.db"))
    ops, messages, refreshes = _ops()
    res = asyncio.run(ops.export_bundle("no-such-id", "/tmp/x.tar.gz"))
    assert not res.ok
    res = asyncio.run(ops.import_bundle("/tmp/x.tar.gz", "", 8070))
    assert not res.ok and "name" in res.message
    assert refreshes == []  # early bails never touch workers
    assert messages


def test_import_unknown_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "t2.db"))
    ops, messages, _ = _ops()
    res = asyncio.run(
        ops.import_bundle(str(tmp_path / "nope.tar.gz"), "N", 8070))
    assert not res.ok
    assert messages
