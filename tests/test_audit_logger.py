#!/usr/bin/env python3
"""Tests for core/audit_logger.py — CSV Audit Trail."""

import csv
import os

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

        with open(path, "r", encoding="utf-8") as f:
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
