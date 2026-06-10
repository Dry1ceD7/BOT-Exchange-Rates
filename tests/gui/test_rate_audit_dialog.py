#!/usr/bin/env python3
"""GUI tests for the Rate Audit report dialog (construction only).

The audit logic + worker are covered by tests/test_rate_audit*.py; here we just
confirm the report Toplevel builds cleanly both with and without changes and
that the module imports. Requires a display; tk_root skips on headless CI.
"""
import contextlib
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import customtkinter as ctk

from core.rate_audit import RateAuditReport, RateChange
from gui.panels import rate_audit_dialog


def _report(*, with_changes: bool, applied: bool = True) -> RateAuditReport:
    r = RateAuditReport(
        file="/tmp/ledger.xlsx", scanned_rows=5, compared_cells=20,
        applied=applied, unverifiable=1,
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


def _toplevels(root):
    return [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel)]


def _find_button(root, text):
    """Depth-first walk for the CTkButton labelled ``text``."""
    stack = list(root.winfo_children())
    while stack:
        w = stack.pop()
        if isinstance(w, ctk.CTkButton):
            with contextlib.suppress(Exception):
                if w.cget("text") == text:
                    return w
        with contextlib.suppress(Exception):
            stack.extend(w.winfo_children())
    return None


def _destroy_toplevels(root):
    for w in _toplevels(root):
        with contextlib.suppress(Exception):
            w.destroy()


class TestReportDialog:
    def test_builds_with_changes(self, tk_root):
        rate_audit_dialog._show_report_dialog(
            tk_root, _report(with_changes=True), "/tmp/Audit_Log_x.csv"
        )
        tops = _toplevels(tk_root)
        assert tops, "report dialog Toplevel was not created"
        for w in tops:
            with contextlib.suppress(Exception):
                w.destroy()

    def test_builds_with_no_changes(self, tk_root):
        rate_audit_dialog._show_report_dialog(
            tk_root, _report(with_changes=False), None
        )
        tops = _toplevels(tk_root)
        assert tops, "report dialog Toplevel was not created"
        for w in tops:
            with contextlib.suppress(Exception):
                w.destroy()

    def test_set_status_is_safe_without_label(self, tk_root):
        # _set_status must no-op (not raise) when the app lacks lbl_status.
        from types import SimpleNamespace
        rate_audit_dialog._set_status(SimpleNamespace(), "hello")


class TestRevertGuard:
    """F11: the report dialog's Revert must route through the app's guarded
    entry (_start_guarded_revert), never call batch_handler.start_revert
    directly — the _exrate_running lease was released before the dialog shows,
    so only the guarded entry re-checks the busy flags."""

    def test_revert_routes_through_guarded_entry(self, tk_root):
        tk_root._start_guarded_revert = MagicMock(return_value=True)
        report = _report(with_changes=True)
        try:
            rate_audit_dialog._show_report_dialog(tk_root, report, None)
            btn = _find_button(tk_root, "Revert these changes")
            assert btn is not None, "Revert button missing from report dialog"
            btn.cget("command")()
            tk_root._start_guarded_revert.assert_called_once_with(
                report.file, report.backup_path,
            )
        finally:
            _destroy_toplevels(tk_root)
            del tk_root._start_guarded_revert

    def test_revert_refusal_keeps_dialog_open_and_says_so(self, tk_root):
        # Guarded entry refuses (e.g. a scheduler-fired batch owns the file):
        # the dialog must stay open and surface the refusal, not silently die.
        tk_root._start_guarded_revert = MagicMock(return_value=False)
        report = _report(with_changes=True)
        try:
            rate_audit_dialog._show_report_dialog(tk_root, report, None)
            btn = _find_button(tk_root, "Revert these changes")
            assert btn is not None
            btn.cget("command")()
            tops = [w for w in _toplevels(tk_root) if w.winfo_exists()]
            assert tops, "dialog was destroyed despite the revert refusal"
            # The refusal text lands in the dialog's own status label.
            labels = []
            stack = list(tops[0].winfo_children())
            while stack:
                w = stack.pop()
                if isinstance(w, ctk.CTkLabel):
                    with contextlib.suppress(Exception):
                        labels.append(str(w.cget("text")))
                with contextlib.suppress(Exception):
                    stack.extend(w.winfo_children())
            assert any("Busy" in s for s in labels)
        finally:
            _destroy_toplevels(tk_root)
            del tk_root._start_guarded_revert
