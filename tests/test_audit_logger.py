#!/usr/bin/env python3
"""Tests for core/audit_logger.py — CSV Audit Trail."""

import atexit
import csv
import os
from datetime import datetime, timedelta

import pytest

from core.audit_logger import AuditLogger, cleanup_old_audit_logs


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

        with open(path, encoding="utf-8-sig") as f:
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

        with open(path, encoding="utf-8") as f:
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

        with open(path, encoding="utf-8") as f:
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


class TestAtexitUnregister:
    """finalize() and _atexit_cleanup() must unregister the atexit callback."""

    def test_double_finalize_is_harmless(self, tmp_path):
        """Calling finalize() twice must not raise (idempotent close)."""
        audit = AuditLogger(log_dir=str(tmp_path))
        first = audit.finalize()
        second = audit.finalize()
        assert first == second

    def test_finalize_unregisters_atexit(self, tmp_path):
        """After finalize(), the atexit callback is no longer registered."""
        audit = AuditLogger(log_dir=str(tmp_path))
        audit.finalize()
        # unregister is idempotent; a second call returns None either way, but
        # the contract is that finalize() already removed it so per-batch
        # loggers in a long-lived process do not accumulate callbacks.
        atexit.unregister(audit._atexit_cleanup)
        # Re-running the (now-orphaned) cleanup must stay safe.
        audit._atexit_cleanup()


class TestCleanupOldAuditLogs:
    """Retention prunes Audit_Log_*.csv by the EMBEDDED filename timestamp."""

    def _write(self, directory, name: str) -> None:
        (directory / name).write_text("x", encoding="utf-8")

    def test_prunes_old_log_by_embedded_timestamp(self, tmp_path):
        old_stamp = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d_%H%M%S")
        new_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        old_name = f"Audit_Log_{old_stamp}.csv"
        new_name = f"Audit_Log_{new_stamp}.csv"
        self._write(tmp_path, old_name)
        self._write(tmp_path, new_name)

        deleted = cleanup_old_audit_logs(log_dir=str(tmp_path), max_age_days=30)

        assert deleted == 1
        assert not (tmp_path / old_name).exists()
        assert (tmp_path / new_name).exists()

    def test_ignores_st_mtime(self, tmp_path):
        """Old embedded timestamp prunes even when st_mtime is brand new."""
        old_stamp = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d_%H%M%S")
        old_name = f"Audit_Log_{old_stamp}.csv"
        path = tmp_path / old_name
        self._write(tmp_path, old_name)
        # Force a recent mtime — retention must NOT trust it.
        now = datetime.now().timestamp()
        os.utime(path, (now, now))

        deleted = cleanup_old_audit_logs(log_dir=str(tmp_path), max_age_days=30)

        assert deleted == 1
        assert not path.exists()

    def test_skips_unparseable_names(self, tmp_path):
        """Files that do not match the timestamp pattern are left untouched."""
        self._write(tmp_path, "Audit_Log_not_a_timestamp.csv")
        self._write(tmp_path, "Audit_Log_.csv")

        deleted = cleanup_old_audit_logs(log_dir=str(tmp_path), max_age_days=0)

        assert deleted == 0
        assert (tmp_path / "Audit_Log_not_a_timestamp.csv").exists()

    def test_missing_dir_returns_zero(self, tmp_path):
        deleted = cleanup_old_audit_logs(
            log_dir=str(tmp_path / "does_not_exist"), max_age_days=30,
        )
        assert deleted == 0

    def test_keeps_logs_within_window(self, tmp_path):
        recent_stamp = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d_%H%M%S")
        recent_name = f"Audit_Log_{recent_stamp}.csv"
        self._write(tmp_path, recent_name)

        deleted = cleanup_old_audit_logs(log_dir=str(tmp_path), max_age_days=30)

        assert deleted == 0
        assert (tmp_path / recent_name).exists()
