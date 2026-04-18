#!/usr/bin/env python3
"""
Tests for core/enterprise.py helpers.
"""

import json
from datetime import date

import openpyxl

from core.enterprise import (
    load_holiday_overlays,
    load_job_history_stats,
    mask_secret,
    record_job_history,
    summarize_validation,
)


class TestMaskSecret:
    def test_masks_with_visible_tail(self):
        assert mask_secret("abcdef1234", visible=4) == "******1234"

    def test_handles_empty(self):
        assert mask_secret("") == ""


class TestHolidayOverlays:
    def test_load_csv(self, tmp_path):
        p = tmp_path / "holidays.csv"
        p.write_text("date,name\n2026-01-01,New Year\n", encoding="utf-8")
        rows = load_holiday_overlays(str(p))
        assert rows == [("2026-01-01", "New Year")]

    def test_load_json(self, tmp_path):
        p = tmp_path / "holidays.json"
        p.write_text(
            json.dumps([{"date": "2026-04-13", "name": "Songkran"}]),
            encoding="utf-8",
        )
        rows = load_holiday_overlays(str(p))
        assert rows == [("2026-04-13", "Songkran")]


class TestValidationSummary:
    def test_summarize_validation_counts(self, tmp_path):
        fp = tmp_path / "ledger.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Jan"
        ws.append(["Date", "Cur", "EX Rate"])
        ws.append([date(2026, 1, 2), "USD", None])
        ws.append(["", "EUR", None])  # missing date
        ws.append([date(2026, 1, 3), "JPY", None])  # unmatched currency
        ws.append([date(2026, 1, 4), "THB", "=IFS(...)"])  # skipped row
        wb.save(fp)
        wb.close()

        summary = summarize_validation([str(fp)])
        assert summary["file_count"] == 1
        assert summary["totals"]["missing_dates"] == 1
        assert summary["totals"]["unmatched_currencies"] == 1
        assert summary["totals"]["skipped_rows"] == 1


class TestJobHistory:
    def test_record_and_stats(self, tmp_path):
        hp = tmp_path / "history.jsonl"
        record_job_history(
            {
                "source": "headless",
                "success": 2,
                "failed": 0,
                "duration_sec": 10.0,
            },
            history_path=str(hp),
        )
        record_job_history(
            {
                "source": "headless",
                "success": 1,
                "failed": 1,
                "duration_sec": 20.0,
            },
            history_path=str(hp),
        )
        stats = load_job_history_stats(history_path=str(hp))
        assert stats["runs"] == 2
        assert stats["success_runs"] == 1
        assert stats["failed_runs"] == 1
        assert stats["avg_runtime_sec"] == 15.0
