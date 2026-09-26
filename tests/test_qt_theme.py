"""PSQ-10.1: system theme detection (offscreen-safe parts headless)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt", reason="pytest-qt required for Qt tests")

from odoo_vite.ui_qt import theme as theme_mod  # noqa: E402
from odoo_vite.ui_qt.theme import (  # noqa: E402
    apply_theme,
    dark_from_value,
    system_prefers_dark,
)


def test_dark_from_value_mapping():
    assert dark_from_value(1) is True
    assert dark_from_value(0) is False
    assert dark_from_value(2) is False
    assert dark_from_value("prefer-dark") is True
    assert dark_from_value("default") is False
    assert dark_from_value(None) is False
    assert dark_from_value("garbage") is False


def test_system_prefers_dark_never_raises(monkeypatch):
    monkeypatch.setattr(theme_mod, "_read_portal",
                        lambda: (_ for _ in ()).throw(Exception("no bus")))
    monkeypatch.setattr(theme_mod, "_read_gsettings", lambda: None)
    assert system_prefers_dark() is False


def test_apply_both_variants_parse(qapp):
    apply_theme(qapp, False)
    light = qapp.styleSheet()
    assert "QListWidget::item:selected" in light
    apply_theme(qapp, True)
    dark = qapp.styleSheet()
    assert dark != light
    assert "#2d2d2d" in dark
    assert "QLabel[class=\"dim\"]" in dark
    # Switching back works (live-change path).
    apply_theme(qapp, False)
    assert qapp.styleSheet() == light


def test_monitor_reports_current(qapp):
    monitor = theme_mod.ThemeMonitor()
    assert isinstance(monitor.dark, bool)
    assert monitor.dark == system_prefers_dark()
