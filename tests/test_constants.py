#!/usr/bin/env python3
"""
tests/test_constants.py
---------------------------------------------------------------------------
Unit tests for core/constants.py — date parsing (Buddhist-Era normalization,
plausible-year bounds, day-first policy) and the BOT business-date helper.
---------------------------------------------------------------------------
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext

import pytest

from core.constants import (
    LEDGER_SUPPORTED_CURRENCIES,
    PER_100_UNIT_CURRENCIES,
    bot_today,
    collect_excel_files,
    format_rate_value,
    parse_date,
    parse_decimal_safe,
)


class TestParseDateBuddhistEra:
    """B.E. years (~2400-2700) are normalized to Common Era."""

    def test_be_slash_format(self):
        # 2567 B.E. == 2024 CE; day-first → 31 Dec 2024.
        assert parse_date("31/12/2567") == date(2024, 12, 31)

    def test_be_iso_format(self):
        assert parse_date("2567-01-15") == date(2024, 1, 15)

    def test_be_dash_format(self):
        assert parse_date("15-01-2567") == date(2024, 1, 15)

    def test_be_lower_band_boundary(self):
        # 2400 B.E. == 1857 CE, which is below the plausible-year floor → None.
        assert parse_date("01/01/2400") is None

    def test_be_recent_year(self):
        # 2568 B.E. == 2025 CE.
        assert parse_date("01/06/2568") == date(2025, 6, 1)


class TestParseDateSanityBounds:
    """Implausible years are rejected rather than silently mis-targeted."""

    def test_year_9999_returns_none(self):
        assert parse_date("01/01/9999") is None

    def test_year_far_future_returns_none(self):
        # Above the B.E. band and far in the future → None.
        assert parse_date("2999-01-01") is None

    def test_year_too_old_returns_none(self):
        # 1969 is below the 1970 floor.
        assert parse_date("1969-12-31") is None


class TestParseDateNormalCe:
    """Plausible CE dates pass through unchanged."""

    def test_iso_ce(self):
        assert parse_date("2025-03-14") == date(2025, 3, 14)

    def test_slash_ce(self):
        assert parse_date("14/03/2025") == date(2025, 3, 14)

    def test_year_floor_1970_kept(self):
        assert parse_date("1970-01-01") == date(1970, 1, 1)

    def test_next_year_allowed(self):
        nxt = date.today().year + 1
        assert parse_date(f"{nxt}-01-01") == date(nxt, 1, 1)


class TestParseDateDayFirst:
    """DATE_FORMATS is day-first by deliberate Thai-locale policy."""

    def test_ambiguous_is_day_first(self):
        # "01/02/2025" is 1 February, NOT 2 January.
        assert parse_date("01/02/2025") == date(2025, 2, 1)


class TestParseDatePassThrough:
    """date/datetime inputs and junk tokens behave as before."""

    def test_datetime_passthrough(self):
        assert parse_date(datetime(2025, 5, 6, 12, 0)) == date(2025, 5, 6)

    def test_date_passthrough(self):
        assert parse_date(date(2025, 5, 6)) == date(2025, 5, 6)

    def test_empty_returns_none(self):
        assert parse_date("") is None

    def test_nan_returns_none(self):
        assert parse_date("nan") is None

    def test_garbage_returns_none(self):
        assert parse_date("not-a-date") is None


class TestLedgerCurrencyConstants:
    """Per-100-unit currencies must NOT be ledger-supported (F4).

    BOT quotes JPY per 100 yen; writing that figure into a ledger EX Rate
    column would overstate an "amount x rate" conversion 100x, so JPY is
    excluded until the department confirms its convention.
    """

    def test_jpy_not_ledger_supported(self):
        assert "JPY" not in LEDGER_SUPPORTED_CURRENCIES

    def test_per_100_unit_currencies_documented(self):
        assert PER_100_UNIT_CURRENCIES == ("JPY",)

    def test_per_100_set_disjoint_from_supported(self):
        # Re-including a per-100 currency requires handling the divide-by-100
        # convention first — the sets must never overlap silently.
        assert not set(PER_100_UNIT_CURRENCIES) & LEDGER_SUPPORTED_CURRENCIES


class TestBotToday:
    """bot_today() returns the Asia/Bangkok (UTC+7) calendar date."""

    def test_matches_bangkok_date(self):
        expected = datetime.now(timezone(timedelta(hours=7))).date()
        assert bot_today() == expected

    def test_returns_date_type(self):
        assert isinstance(bot_today(), date)


class TestFormatRateValueRoundingMode:
    """ROUND_HALF_EVEN is the pinned project standard for 4dp quantization.

    Boundary values are exact decimal ties at the 5th decimal place, chosen
    so HALF_EVEN and HALF_UP disagree — locking the mode, not just the math.
    """

    def test_tie_with_even_digit_rounds_down(self):
        # HALF_EVEN -> 34.5678 (8 is even); HALF_UP would give 34.5679.
        assert format_rate_value(Decimal("34.56785")) == "34.5678"

    def test_tie_with_even_digit_rounds_down_small(self):
        # HALF_EVEN -> 1.2344 (4 is even); HALF_UP would give 1.2345.
        assert format_rate_value(Decimal("1.23445")) == "1.2344"

    def test_tie_with_odd_digit_rounds_up(self):
        # HALF_EVEN -> 1.2344 (3 is odd, rounds to even 4) — same as HALF_UP.
        assert format_rate_value(Decimal("1.23435")) == "1.2344"

    def test_non_tie_rounds_normally(self):
        assert format_rate_value(Decimal("34.56789")) == "34.5679"

    def test_pads_short_values_to_4dp(self):
        assert format_rate_value(Decimal("1.2")) == "1.2000"

    def test_mode_pinned_against_ambient_context(self):
        # Pre-pin, the quantize inherited the ambient decimal context; a
        # HALF_UP context would have flipped the tie to 34.5679. The
        # explicit rounding= argument must make the context irrelevant.
        with localcontext() as ctx:
            ctx.rounding = ROUND_HALF_UP
            assert format_rate_value(Decimal("34.56785")) == "34.5678"

    def test_none_returns_empty_string(self):
        assert format_rate_value(None) == ""

    def test_float_path_unchanged(self):
        assert format_rate_value(33.1234) == "33.1234"


class TestParseDecimalSafeNoRounding:
    """parse_decimal_safe preserves literal digits — no quantize, no mode."""

    def test_preserves_five_decimal_places(self):
        result = parse_decimal_safe("34.56785")
        assert result == Decimal("34.56785")
        assert str(result) == "34.56785"

    def test_strips_whitespace(self):
        assert parse_decimal_safe("  34.5678 ") == Decimal("34.5678")

    def test_empty_returns_none(self):
        assert parse_decimal_safe("") is None
        assert parse_decimal_safe(None) is None

    def test_non_numeric_returns_none(self):
        assert parse_decimal_safe("not-a-rate") is None


class TestCollectExcelFiles:
    """Shared directory-listing helper (main.py / scheduler / GUI sites)."""

    def test_directory_sorted_full_paths_dotfiles_skipped(self, tmp_path):
        (tmp_path / "b.xlsx").write_text("x")
        (tmp_path / "a.xlsm").write_text("x")
        (tmp_path / ".hidden.xlsx").write_text("x")
        (tmp_path / "notes.txt").write_text("x")
        result = collect_excel_files(str(tmp_path))
        assert result == [
            os.path.join(str(tmp_path), "a.xlsm"),
            os.path.join(str(tmp_path), "b.xlsx"),
        ]

    def test_single_excel_file_yields_itself(self, tmp_path):
        fp = tmp_path / "ledger.xlsx"
        fp.write_text("x")
        assert collect_excel_files(str(fp)) == [str(fp)]

    def test_single_non_excel_file_yields_empty(self, tmp_path):
        fp = tmp_path / "ledger.csv"
        fp.write_text("x")
        assert collect_excel_files(str(fp)) == []

    def test_case_insensitive_extension_match(self, tmp_path):
        (tmp_path / "UPPER.XLSX").write_text("x")
        result = collect_excel_files(str(tmp_path))
        assert result == [os.path.join(str(tmp_path), "UPPER.XLSX")]

    def test_dedup_false_returns_raw_listing(self, tmp_path):
        (tmp_path / "a.xlsx").write_text("x")
        assert collect_excel_files(str(tmp_path), dedup=False) == [
            os.path.join(str(tmp_path), "a.xlsx"),
        ]

    def test_missing_directory_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            collect_excel_files(str(tmp_path / "nope"))


class TestCollectExcelFilesRejected:
    """collect_rejected surfaces present-but-unsupported spreadsheet files.

    A folder of legacy .xls exports previously produced an empty listing
    indistinguishable from an empty folder, so every caller (GUI folder
    drop, headless --input, scheduler watch paths) reported a misleading
    'no Excel files found' with no remedy.
    """

    def test_directory_lists_unsupported_spreadsheets(self, tmp_path):
        (tmp_path / "a.xlsx").write_text("x")
        (tmp_path / "Sale Report 2026.xls").write_text("x")
        (tmp_path / "book.xlsb").write_text("x")
        (tmp_path / "notes.txt").write_text("x")

        files, rejected = collect_excel_files(
            str(tmp_path), collect_rejected=True,
        )
        assert files == [os.path.join(str(tmp_path), "a.xlsx")]
        assert sorted(os.path.basename(r) for r in rejected) == [
            "Sale Report 2026.xls", "book.xlsb",
        ]

    def test_single_unsupported_file_is_rejected(self, tmp_path):
        fp = tmp_path / "legacy.xls"
        fp.write_text("x")
        files, rejected = collect_excel_files(str(fp), collect_rejected=True)
        assert files == []
        assert rejected == [str(fp)]

    def test_single_supported_file_yields_no_rejects(self, tmp_path):
        fp = tmp_path / "ledger.xlsx"
        fp.write_text("x")
        files, rejected = collect_excel_files(str(fp), collect_rejected=True)
        assert files == [str(fp)]
        assert rejected == []

    def test_default_signature_unchanged(self, tmp_path):
        """Without collect_rejected the helper still returns a plain list."""
        (tmp_path / "a.xlsx").write_text("x")
        (tmp_path / "old.xls").write_text("x")
        result = collect_excel_files(str(tmp_path))
        assert result == [os.path.join(str(tmp_path), "a.xlsx")]


class TestHumanizeBadZipFile:
    """BadZipFile translates to an actionable save-as-.xlsx message."""

    def test_badzipfile_gets_conversion_remedy(self):
        import zipfile

        from core.constants import humanize_save_error

        msg = humanize_save_error(
            "Sale Report 2026.xlsx", zipfile.BadZipFile("File is not a zip file"),
        )
        assert msg is not None
        assert "Sale Report 2026.xlsx" in msg
        assert ".xlsx" in msg
        # Must mention the legacy-format cause so an accountant knows the fix.
        assert ".xls" in msg

    def test_other_non_oserror_still_returns_none(self):
        from core.constants import humanize_save_error

        assert humanize_save_error("f.xlsx", ValueError("boom")) is None
