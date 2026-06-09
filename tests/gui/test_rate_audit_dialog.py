#!/usr/bin/env python3
"""GUI tests for the Rate Audit report dialog (construction only).

The audit logic + worker are covered by tests/test_rate_audit*.py; here we just
confirm the report Toplevel builds cleanly both with and without changes and
that the module imports. Requires a display; tk_root skips on headless CI.
"""
import contextlib
from datetime import date
from decimal import Decimal

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
