#!/usr/bin/env python3
"""Tests for core/audit_logger.py — CSV Audit Trail."""

import csv
import os

import pytest

from core.audit_logger import AuditLogger


class TestAuditLogger:
    """Test audit log creation and row recording."""

    def test_creates_csv_file(self, tmp_path):
        audit = AuditLogger(log_dir=str(tmp_path))
        path = audit.finalize()
        assert os.path.exists(path)
        assert path.endswith(".csv")

    def test_csv_has_correct_headers(self, tmp_path):
        audit = AuditLogger(log_dir=str(tmp_path))
        path = audit.finalize()

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        assert headers == AuditLogger.HEADERS

    def test_rows_appended_correctly(self, tmp_path):
        audit = AuditLogger(log_dir=str(tmp_path))
        audit.log_row_change(
            filename="ledger.xlsx",
            sheet="January",
            row=5,
            cell_date="2025-01-15",
            currency="USD",
            original_value="",
            new_value="=XLOOKUP(...)",
            rate_source="API",
            holiday_rollback=False,
            anomaly_flag=False,
        )
        audit.log_row_change(
            filename="ledger.xlsx",
            sheet="January",
            row=6,
            cell_date="2025-01-16",
            currency="EUR",
            original_value="=OLD_FORMULA",
            new_value="=NEW_FORMULA",
            rate_source="Cache",
            holiday_rollback=True,
            anomaly_flag=True,
        )
        path = audit.finalize()

        assert audit.row_count == 2

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)  # skip headers
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0][1] == "ledger.xlsx"
        assert rows[0][5] == "USD"
        assert rows[0][9] == "No"       # holiday_rollback
        assert rows[0][10] == ""         # anomaly_flag
        assert rows[1][9] == "Yes"       # holiday_rollback
        assert rows[1][10] == "ANOMALY"  # anomaly_flag

    def test_batch_summary(self, tmp_path):
        audit = AuditLogger(log_dir=str(tmp_path))
        audit.log_batch_summary(
            total_files=5, success=4, failed=1, anomalies_detected=2,
        )
        path = audit.finalize()

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "BATCH SUMMARY" in content
        assert "Files: 5" in content

    def test_finalize_returns_path(self, tmp_path):
        audit = AuditLogger(log_dir=str(tmp_path))
        path = audit.finalize()
        assert isinstance(path, str)
        assert "Audit_Log_" in path

    def test_utf8_sig_bom_present(self, tmp_path):
        """Audit log must be written with a UTF-8 BOM for Excel/Thai text."""
        audit = AuditLogger(log_dir=str(tmp_path))
        path = audit.finalize()
        with open(path, "rb") as f:
            head = f.read(3)
        assert head == b"\xef\xbb\xbf"

    def test_formula_injection_neutralized(self, tmp_path):
        """A leading-= cell must be quoted so spreadsheets treat it as text."""
        audit = AuditLogger(log_dir=str(tmp_path))
        audit.log_row_change(
            filename="ledger.xlsx",
            sheet="Jan",
            row=5,
            cell_date="2025-01-15",
            currency="USD",
            original_value="=cmd|'/c calc'!A1",
            new_value="35.1150",
            rate_source="API",
        )
        path = audit.finalize()
        with open(path, encoding="utf-8-sig") as f:
            next(csv.reader(f))  # header
            row = next(csv.reader(f))
        assert row[6] == "'=cmd|'/c calc'!A1"

    def test_log_after_finalize_raises(self, tmp_path):
        """Logging into a finalized log must raise, not silently no-op."""
        audit = AuditLogger(log_dir=str(tmp_path))
        audit.finalize()
        with pytest.raises(ValueError):
            audit.log_row_change(
                filename="x.xlsx", sheet="s", row=1, cell_date="2025-01-01",
                currency="USD", original_value="", new_value="1",
            )
        with pytest.raises(ValueError):
            audit.log_batch_summary(
                total_files=1, success=1, failed=0, anomalies_detected=0,
            )
