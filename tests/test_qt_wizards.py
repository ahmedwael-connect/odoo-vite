"""PSQ-9: wizard validation + structure (offscreen, pytest-qt).

Heavy provisioning runs live in E2E; here the per-page contracts:
validatePage gates, definition parsing, draft-safety hooks exist.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt.widgets.wizard import WorkerPage  # noqa: E402
from odoo_vite.ui_qt.wizards.adopt_instance import AdoptWizard  # noqa: E402
from odoo_vite.ui_qt.wizards.create_instance import CreateWizard  # noqa: E402
from odoo_vite.ui_qt.wizards.scaffold_module import ScaffoldWizard  # noqa: E402


@pytest.fixture()
def _seeded(tmp_path, monkeypatch):
    from odoo_vite.core.instance import Instance
    from odoo_vite.core.registry import create_instance

    monkeypatch.setenv("ODOO_VITE_DB", str(tmp_path / "qt-wiz.db"))
    inst = Instance(name="WizTaken", version="17.0", path=str(tmp_path),
                    port=8098, primary_db="d")
    assert create_instance(inst).ok
    return inst


def test_create_details_validation(qapp, qtbot, _seeded):
    wiz = CreateWizard()
    qtbot.addWidget(wiz)
    page = wiz.details_page
    assert page.validatePage() is False  # empty name
    page.entry_name.setText("WizTaken")
    page.entry_dbname.setText("d")
    assert page.validatePage() is False  # duplicate name
    page.entry_name.setText("WizFresh")
    page.entry_dbname.setText("bad-name!")
    assert page.validatePage() is False  # invalid identifier
    page.entry_dbname.setText("wdb")
    assert page.validatePage() is True


def test_create_wizard_structure(qapp, qtbot):
    wiz = CreateWizard()
    qtbot.addWidget(wiz)
    assert len(wiz.pageIds()) == 4
    assert hasattr(wiz, "instanceCreated")
    prov = wiz.page(wiz.pageIds()[-1])
    assert isinstance(prov, WorkerPage)
    assert prov.isComplete() is False


def test_version_page_requires_selection(qapp, qtbot):
    wiz = CreateWizard()
    qtbot.addWidget(wiz)
    assert wiz.version_page.validatePage() is False
    wiz.version_page.branch_list.set_items([{"id": "17.0", "title": "17.0"}])
    assert wiz.version_page.branch_list.select_id("17.0")
    assert wiz.version_page.validatePage() is True
    assert wiz.version_page.selected_version() == "17.0"


def test_adopt_locate_validation(qapp, qtbot, tmp_path, _seeded):
    wiz = AdoptWizard()
    qtbot.addWidget(wiz)
    page = wiz.locate_page
    assert page.validatePage() is False  # empty everything
    page.entry_name.setText("WizTaken")
    assert page.validatePage() is False  # duplicate name
    page.entry_name.setText("AdoptMe")
    assert page.validatePage() is False  # no files yet
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_user = odoo\n")
    comm = tmp_path / "community"
    comm.mkdir()
    (comm / "odoo-bin").touch()
    page.entry_conf.setText(str(conf))
    page.entry_community.setText(str(comm))
    assert page.validatePage() is True
    assert "conf parsed" in page.lbl_detected.text()


def test_adopt_gaps_require_db(qapp, qtbot):
    wiz = AdoptWizard()
    qtbot.addWidget(wiz)
    assert wiz.gaps_page.validatePage() is False
    wiz.gaps_page.entry_db.setText("adopted_db")
    assert wiz.gaps_page.validatePage() is True


def test_scaffold_definition_and_validation(qapp, qtbot):
    wiz = ScaffoldWizard()
    qtbot.addWidget(wiz)
    page = wiz.definition_page
    page.initializePage()
    assert page.validatePage() is False  # empty tech name
    page.entry_tech.setText("Bad-Name!")
    page.entry_dest.setText("/tmp/x")
    page.entry_db.setText("d")
    assert page.validatePage() is False
    page.entry_tech.setText("my_lib")
    page.entry_model.setText("library.book")
    page.text_fields.setPlainText("name:char\npages:integer\nbogus-no-colon")
    definition = page.definition()
    assert definition["technical_name"] == "my_lib"
    assert definition["models"][0]["fields"] == [
        {"name": "name", "type": "char"},
        {"name": "pages", "type": "integer"}]
    # DB empty -> invalid (acceptance target required).
    page.entry_db.clear()
    assert page.validatePage() is False
    # Acceptance needs a real existing DB (validatePage probes existence;
    # read-only). 'postgres' always exists.
    page.entry_db.setText("postgres")
    assert page.validatePage() is True


def test_worker_page_completion(qapp, qtbot):
    wiz = CreateWizard()
    qtbot.addWidget(wiz)
    prov = wiz.page(wiz.pageIds()[-1])
    assert prov.isComplete() is False
    prov.set_complete(True)
    assert prov.isComplete() is True


def test_branches_payload_normalizes_list_shape(qapp, qtbot, monkeypatch):
    """A.2: core returns data as a bare list; the page needs a dict.
    The adapter must normalize, or the list stays empty while the
    count (message) reads fine."""
    from odoo_vite.core.result import Result
    from odoo_vite.ui_qt.wizards import create_instance as ci_mod

    monkeypatch.setattr(
        ci_mod.git_manager, "list_odoo_branches",
        lambda search="": Result(ok=True, message="2 branches",
                                 data=["17.0", "18.0"]))
    res = ci_mod._branches_payload("")
    assert res.ok
    assert res.data == {"branches": ["17.0", "18.0"]}


def test_version_page_populates_from_list_payload(qapp, qtbot, monkeypatch):
    """End-to-end through the real worker chain: list-shaped core data
    must still reach visible rows (A.2 regression)."""
    from odoo_vite.core.result import Result
    from odoo_vite.ui_qt.wizards import create_instance as ci_mod

    monkeypatch.setattr(
        ci_mod.git_manager, "list_odoo_branches",
        lambda search="": Result(ok=True, message="2 branches",
                                 data=["17.0", "18.0"]))
    wiz = CreateWizard()
    qtbot.addWidget(wiz)
    wiz.version_page.load_branches()
    qtbot.wait(3000)
    assert wiz.version_page.branch_list.visible_count() == 2
    assert "2 branches" in wiz.version_page.status.text()
