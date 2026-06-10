#!/usr/bin/env python3
"""
tests/gui/test_i18n_gui.py
---------------------------------------------------------------------------
Widget-level tests proving the i18n wiring reaches real GUI surfaces.

When the active language is Thai, the panels must render their Thai catalog
strings (header, buttons, labels) rather than the English literals. These
tests construct each wired surface with language='th' and compare the
rendered widget text to the catalog's Thai entry, then construct it again
with language='en' to confirm the same widget shows the English literal.

The settings modal additionally exposes a language selector whose choice is
persisted and pushed into the i18n cache on Save — that round-trip is
verified here too.

All tests require a display; the tk_root fixture skips them on headless CI.
SettingsManager is mocked so no real settings.json is read or written, and an
autouse fixture resets the i18n language cache so Thai never leaks into the
English-literal assertions elsewhere in the GUI suite.
"""

from unittest.mock import MagicMock, patch

import pytest

import core.i18n as i18n
from core.i18n import CATALOG, DEFAULT_LANGUAGE, set_language

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def _reset_language():
    """Reset the process-wide i18n cache before and after every test here."""
    i18n._active_language = None
    set_language(DEFAULT_LANGUAGE)
    yield
    i18n._active_language = None
    set_language(DEFAULT_LANGUAGE)


def _th(key: str) -> str:
    return CATALOG[key]["th"]


def _en(key: str) -> str:
    return CATALOG[key]["en"]


def _find_button(widget, text):
    """Recursive search for a CTkButton whose text exactly equals ``text``."""
    import customtkinter as ctk

    result = []

    def _walk(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkButton) and (
                str(child.cget("text")) == text
            ):
                result.append(child)
            _walk(child)

    _walk(widget)
    return result[0] if result else None


# ---------------------------------------------------------------------------
# Settings modal
# ---------------------------------------------------------------------------
def _make_modal(tk_root, language):
    from gui.panels.settings_modal import SettingsModal

    set_language(language)
    mock_mgr = MagicMock()
    mock_mgr.load.return_value = {
        "appearance": "system",
        "auto_update": True,
        "rate_type": "buying_transfer",
        "anomaly_threshold_pct": 5.0,
        "language": language,
    }
    with patch(
        "gui.panels.settings_modal.SettingsManager", return_value=mock_mgr
    ):
        modal = SettingsModal(tk_root)
    modal.withdraw()
    return modal, mock_mgr


class TestSettingsModalTranslated:
    def test_title_is_thai(self, tk_root):
        modal, _ = _make_modal(tk_root, "th")
        try:
            assert modal.title() == _th("settings.title")
        finally:
            modal.destroy()

    def test_save_button_is_thai(self, tk_root):
        modal, _ = _make_modal(tk_root, "th")
        try:
            assert _find_button(modal, _th("settings.btn_save")) is not None
        finally:
            modal.destroy()

    def test_save_button_is_english_when_en(self, tk_root):
        modal, _ = _make_modal(tk_root, "en")
        try:
            assert _find_button(modal, _en("settings.btn_save")) is not None
        finally:
            modal.destroy()

    def test_language_selector_present_and_defaults(self, tk_root):
        modal, _ = _make_modal(tk_root, "en")
        try:
            assert hasattr(modal, "_language_var")
            # The selector shows display NAMES; English maps to "English".
            from core.i18n import LANGUAGE_LABELS

            assert modal._language_var.get() == LANGUAGE_LABELS["en"]
        finally:
            modal.destroy()

    def test_save_persists_language_and_applies_live(self, tk_root):
        from core.i18n import LANGUAGE_LABELS, get_language

        modal, mock_mgr = _make_modal(tk_root, "en")
        # User picks Thai in the selector, then saves. _save_and_close()
        # destroys the modal itself, so no explicit teardown is needed.
        modal._language_var.set(LANGUAGE_LABELS["th"])
        modal._save_and_close()
        # Persisted as the lowercase code...
        saved = mock_mgr.save.call_args[0][0]
        assert saved["language"] == "th"
        # ...and pushed into the i18n cache so new surfaces use Thai.
        assert get_language() == "th"


# ---------------------------------------------------------------------------
# Scheduler panel
# ---------------------------------------------------------------------------
def _make_panel(tk_root, language):
    from gui.panels.scheduler_panel import SchedulerPanel

    set_language(language)
    mock_mgr = MagicMock()
    mock_mgr.load.return_value = {}
    mock_mgr.save = MagicMock()
    with patch(
        "gui.panels.scheduler_panel.SettingsManager", return_value=mock_mgr
    ):
        panel = SchedulerPanel(tk_root)
    return panel


class TestSchedulerPanelTranslated:
    def test_title_label_is_thai(self, tk_root):
        panel = _make_panel(tk_root, "th")
        try:
            assert panel._lbl_title.cget("text") == _th("sched.title")
        finally:
            panel.destroy()

    def test_add_button_is_thai(self, tk_root):
        panel = _make_panel(tk_root, "th")
        try:
            assert panel._btn_add.cget("text") == _th("sched.btn_add")
        finally:
            panel.destroy()

    def test_add_button_is_english_when_en(self, tk_root):
        panel = _make_panel(tk_root, "en")
        try:
            assert panel._btn_add.cget("text") == _en("sched.btn_add")
        finally:
            panel.destroy()

    def test_status_no_folders_is_thai(self, tk_root):
        panel = _make_panel(tk_root, "th")
        try:
            panel._update_status()
            assert panel._lbl_status.cget("text") == _th(
                "sched.status_no_folders"
            )
        finally:
            panel.destroy()


# ---------------------------------------------------------------------------
# Token registration dialog
# ---------------------------------------------------------------------------
def _make_token_dialog(tk_root, language, tmp_path):
    from gui.panels.token_dialog import TokenRegistrationDialog

    set_language(language)
    env_path = str(tmp_path / ".env")
    dialog = TokenRegistrationDialog(tk_root, env_path=env_path)
    dialog.withdraw()
    return dialog


class TestTokenDialogTranslated:
    def test_test_button_is_thai(self, tk_root, tmp_path):
        dialog = _make_token_dialog(tk_root, "th", tmp_path)
        try:
            assert dialog._btn_test.cget("text") == _th("token.btn_test")
        finally:
            dialog.destroy()

    def test_activate_button_is_thai(self, tk_root, tmp_path):
        dialog = _make_token_dialog(tk_root, "th", tmp_path)
        try:
            assert dialog._btn_activate.cget("text") == _th(
                "token.btn_activate"
            )
        finally:
            dialog.destroy()

    def test_buttons_english_when_en(self, tk_root, tmp_path):
        dialog = _make_token_dialog(tk_root, "en", tmp_path)
        try:
            assert dialog._btn_test.cget("text") == _en("token.btn_test")
            assert dialog._btn_activate.cget("text") == _en(
                "token.btn_activate"
            )
        finally:
            dialog.destroy()


# ---------------------------------------------------------------------------
# CSV panel
# ---------------------------------------------------------------------------
class TestCsvPanelTranslated:
    def _make(self, tk_root, language):
        from gui.panels.csv_panel import CSVPanel

        set_language(language)
        return CSVPanel(tk_root)

    def test_import_button_is_thai(self, tk_root):
        panel = self._make(tk_root, "th")
        try:
            assert panel._btn_import.cget("text") == _th("csv.btn_import")
            assert panel._btn_export.cget("text") == _th("csv.btn_export")
        finally:
            panel.destroy()

    def test_import_button_is_english_when_en(self, tk_root):
        panel = self._make(tk_root, "en")
        try:
            assert panel._btn_import.cget("text") == _en("csv.btn_import")
        finally:
            panel.destroy()


# ---------------------------------------------------------------------------
# Rate ticker
# ---------------------------------------------------------------------------
class TestRateTickerTranslated:
    def _make(self, tk_root, language):
        from gui.panels.rate_ticker import RateTicker

        set_language(language)
        # cache_db=None: the connecting label is painted at build time and
        # start() is never called, so no worker thread spawns here.
        return RateTicker(tk_root, cache_db=None)

    def test_connecting_label_is_thai(self, tk_root):
        ticker = self._make(tk_root, "th")
        try:
            assert ticker.lbl_live.cget("text") == _th("ticker.connecting")
        finally:
            ticker.destroy()

    def test_connecting_label_is_english_when_en(self, tk_root):
        ticker = self._make(tk_root, "en")
        try:
            assert ticker.lbl_live.cget("text") == _en("ticker.connecting")
        finally:
            ticker.destroy()


# ---------------------------------------------------------------------------
# ExRate dialog
# ---------------------------------------------------------------------------
def _open_exrate_dialog(tk_root, language):
    """Build show_exrate_dialog's Toplevel withdrawn/non-modal (no display
    grab), mirroring tests/gui/test_exrate_dialog.py's harness."""
    import customtkinter as ctk

    from gui.panels.exrate_dialog import show_exrate_dialog

    set_language(language)
    captured = []
    _orig_init = ctk.CTkToplevel.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.withdraw()
        captured.append(self)

    with (
        patch.object(ctk.CTkToplevel, "__init__", _patched_init),
        patch.object(ctk.CTkToplevel, "grab_set", lambda self: None),
        patch.object(ctk.CTkToplevel, "transient", lambda self, *a: None),
        patch.object(ctk.CTkToplevel, "update_idletasks", lambda self: None),
    ):
        show_exrate_dialog(tk_root)
    assert captured, "CTkToplevel was not instantiated by show_exrate_dialog"
    return captured[0]


def _walk_widgets(root):
    import contextlib

    stack = list(root.winfo_children())
    while stack:
        w = stack.pop()
        yield w
        with contextlib.suppress(Exception):
            stack.extend(w.winfo_children())


def _button_texts(root):
    import customtkinter as ctk

    return [
        str(w.cget("text")) for w in _walk_widgets(root)
        if isinstance(w, ctk.CTkButton)
    ]


def _label_texts(root):
    import customtkinter as ctk

    return [
        str(w.cget("text")) for w in _walk_widgets(root)
        if isinstance(w, ctk.CTkLabel)
    ]


class TestExrateDialogTranslated:
    def test_title_and_create_button_are_thai(self, tk_root):
        dialog = _open_exrate_dialog(tk_root, "th")
        try:
            assert dialog.title() == _th("exrate.window_title")
            assert _th("exrate.btn_create") in _button_texts(dialog)
        finally:
            dialog.destroy()

    def test_create_button_is_english_when_en(self, tk_root):
        dialog = _open_exrate_dialog(tk_root, "en")
        try:
            assert _en("exrate.btn_create") in _button_texts(dialog)
        finally:
            dialog.destroy()


# ---------------------------------------------------------------------------
# Rate Audit report dialog
# ---------------------------------------------------------------------------
def _audit_report(with_changes: bool):
    from datetime import date
    from decimal import Decimal

    from core.rate_audit import RateAuditReport, RateChange

    r = RateAuditReport(
        file="/tmp/ledger.xlsx", scanned_rows=5, compared_cells=20,
        applied=True, unverifiable=0,
    )
    r.backup_path = "/tmp/ledger.bak.xlsx"
    if with_changes:
        r.changes.append(RateChange(
            row=2, col=2, cell="B2", rate_date=date(2026, 5, 27),
            column_label="USD Buying TT Rate", currency="USD",
            rate_type="buying_transfer", old_value=Decimal("32.0000"),
            new_value=Decimal("32.4507"),
            reason="value 32.0000 != BOT buying_transfer 32.4507",
        ))
    return r


def _open_audit_report(tk_root, language, with_changes=True):
    import contextlib

    import customtkinter as ctk

    from gui.panels import rate_audit_dialog

    set_language(language)
    before = set(tk_root.winfo_children())
    rate_audit_dialog._show_report_dialog(
        tk_root, _audit_report(with_changes), "/tmp/Audit_Log_x.csv"
    )
    new = [
        w for w in tk_root.winfo_children()
        if w not in before and isinstance(w, ctk.CTkToplevel)
    ]
    assert new, "report dialog Toplevel was not created"
    dlg = new[0]
    with contextlib.suppress(Exception):
        dlg.withdraw()
    return dlg


class TestRateAuditDialogTranslated:
    def test_title_heading_and_buttons_are_thai(self, tk_root):
        dlg = _open_audit_report(tk_root, "th")
        try:
            assert dlg.title() == _th("rateaudit.report_title")
            assert _th("rateaudit.btn_revert") in _button_texts(dlg)
            assert _th("rateaudit.btn_close") in _button_texts(dlg)
            expected_head = _th("rateaudit.head_corrected").format(
                count=1, fname="ledger.xlsx"
            )
            assert expected_head in _label_texts(dlg)
        finally:
            dlg.destroy()

    def test_no_changes_body_is_thai(self, tk_root):
        dlg = _open_audit_report(tk_root, "th", with_changes=False)
        try:
            assert _th("rateaudit.no_corrections") in _label_texts(dlg)
        finally:
            dlg.destroy()

    def test_buttons_are_english_when_en(self, tk_root):
        dlg = _open_audit_report(tk_root, "en")
        try:
            assert _en("rateaudit.btn_revert") in _button_texts(dlg)
            assert _en("rateaudit.btn_close") in _button_texts(dlg)
        finally:
            dlg.destroy()


# ---------------------------------------------------------------------------
# Catalog completeness for the new rateaudit.* namespace
# ---------------------------------------------------------------------------
class TestRateAuditCatalogComplete:
    def test_every_rateaudit_key_has_en_and_th(self):
        keys = [k for k in CATALOG if k.startswith("rateaudit.")]
        assert keys, "rateaudit.* namespace missing from the catalog"
        for key in keys:
            assert CATALOG[key].get("en", "").strip(), key
            assert CATALOG[key].get("th", "").strip(), key

    def test_verify_rates_button_key_has_en_and_th(self):
        entry = CATALOG["main.btn_verify_rates"]
        assert entry["en"] == "Verify Rates"
        assert entry["th"].strip()
