#!/usr/bin/env python3
"""
tests/test_engine_multicurrency.py
---------------------------------------------------------------------------
Regression coverage for LedgerEngine.update_exrate_standalone CUSTOM
multi-currency path (e.g. GBP/JPY with arbitrary rate_types + date_range).

Verifies:
  - headers + values land in the correct columns,
  - rate values are written as EXACT Decimals (Mathematical Truth, no float
    round-trip / approx),
  - the ExRate sheet content is cleared and rewritten,
  - the disk-space OSError guard fires BEFORE the in-place overwrite, leaving
    the original file untouched on disk.

All API access is mocked; backup + cache are injected as temp instances so
no real network, no real singleton state, no real backup dir is touched.
---------------------------------------------------------------------------
"""

import asyncio
from collections import namedtuple
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

import core.engine as engine_mod
from core.backup_manager import BackupManager
from core.engine import LedgerEngine

_DiskUsage = namedtuple("_DiskUsage", ["total", "used", "free"])


def _cell_decimal(value) -> Decimal:
    """Recover the exact 4dp Decimal value from a reloaded cell.

    openpyxl serializes numeric cell values to float on save and returns a
    float on reload, so the in-memory Decimal type does not survive a
    round-trip. Going through str() recovers the shortest exact decimal that
    reproduces the float, which equals the original 4dp value for BOT rates.
    """
    return Decimal(str(value))


# =========================================================================
#  FIXTURES
# =========================================================================

@pytest.fixture
def temp_backup(tmp_path):
    """A BackupManager rooted in a temp dir (never touches the real data/)."""
    return BackupManager(backup_dir=str(tmp_path / "backups"))


@pytest.fixture
def exrate_file(tmp_path):
    """A standalone ExRate workbook with pre-existing junk content.

    Pre-seeds an old header + an old data row so we can prove the custom
    writer CLEARS the prior content before rewriting.
    """
    filepath = tmp_path / "ExRate_standalone.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ExRate"
    # Stale content from a previous run — must be wiped.
    ws.append(["OLD-DATE", "OLD-USD", "OLD-EUR"])
    ws.append([date(1999, 1, 1), 1.1111, 2.2222])
    wb.save(str(filepath))
    wb.close()
    return str(filepath)


def _make_currency_side_effect(per_ccy):
    """Build an async side_effect for api.get_exchange_rates keyed on currency.

    per_ccy maps currency -> list of SimpleNamespace rate records. Any
    currency not present (e.g. USD/EUR fetched by the holiday preload step)
    returns an empty list.
    """
    async def _side_effect(start, end, currency):
        return list(per_ccy.get(currency, []))

    return _side_effect


def _make_api():
    """A mocked API matching the BOTClient async surface."""
    api = AsyncMock()
    api.get_holidays = AsyncMock(return_value=[])
    api.get_exchange_rates = AsyncMock(return_value=[])
    return api


# =========================================================================
#  CUSTOM MULTI-CURRENCY PATH
# =========================================================================

class TestCustomMultiCurrency:
    """update_exrate_standalone custom path (GBP/JPY, mixed rate types)."""

    def _run(self, exrate_file, temp_backup, tmp_cache, per_ccy,
             rate_types, currencies, date_range):
        api = _make_api()
        api.get_exchange_rates = AsyncMock(
            side_effect=_make_currency_side_effect(per_ccy)
        )
        eng = LedgerEngine(api, backup=temp_backup, cache=tmp_cache)
        return eng, asyncio.run(
            eng.update_exrate_standalone(
                exrate_file,
                currencies=currencies,
                rate_types=rate_types,
                date_range=date_range,
            )
        )

    def test_headers_and_values_land_in_right_columns(
        self, exrate_file, temp_backup, tmp_cache,
    ):
        # Two-day window so the layout stays small + deterministic.
        dr = (date(2025, 3, 10), date(2025, 3, 11))
        rate_types = {"Buying TT": "buying_transfer", "Selling": "selling"}
        currencies = ["GBP", "JPY"]

        # EXACT Decimals fed via the rate records — the engine writes them
        # straight through to the cells (no float coercion in the custom path).
        gbp_records = [
            SimpleNamespace(
                period="2025-03-10", currency="GBP",
                buying_transfer=Decimal("42.1234"),
                selling=Decimal("43.5678"),
                buying_sight=None, mid_rate=None,
            ),
            SimpleNamespace(
                period="2025-03-11", currency="GBP",
                buying_transfer=Decimal("42.2200"),
                selling=Decimal("43.6600"),
                buying_sight=None, mid_rate=None,
            ),
        ]
        jpy_records = [
            SimpleNamespace(
                period="2025-03-10", currency="JPY",
                buying_transfer=Decimal("0.2155"),
                selling=Decimal("0.2233"),
                buying_sight=None, mid_rate=None,
            ),
        ]
        per_ccy = {"GBP": gbp_records, "JPY": jpy_records}

        _eng, out = self._run(
            exrate_file, temp_backup, tmp_cache, per_ccy,
            rate_types, currencies, dr,
        )
        assert out == exrate_file

        wb = openpyxl.load_workbook(out)
        ws = wb["ExRate"]

        # ── Headers: Date | GBP Buying TT | GBP Selling | JPY Buying TT |
        #            JPY Selling | Holidays/Weekend ──────────────────────
        header_row = [ws.cell(row=1, column=c).value for c in range(1, 7)]
        assert header_row == [
            "Date",
            "GBP Buying TT", "GBP Selling",
            "JPY Buying TT", "JPY Selling",
            "Holidays/Weekend",
        ]

        # ── Row 2 = 2025-03-10 (Monday) ───────────────────────────────
        row2 = {
            "date": ws.cell(row=2, column=1).value,
            "gbp_tt": ws.cell(row=2, column=2).value,
            "gbp_sell": ws.cell(row=2, column=3).value,
            "jpy_tt": ws.cell(row=2, column=4).value,
            "jpy_sell": ws.cell(row=2, column=5).value,
        }
        assert row2["date"] in (date(2025, 3, 10),
                                datetime(2025, 3, 10))
        # GBP lands in cols 2/3, JPY in cols 4/5 — never crossed.
        # NOTE: openpyxl serializes numeric cells back to float on reload, so
        # we compare the exact 4dp VALUE via Decimal(str(cell)). See the
        # `issues` note about Decimal type not surviving the round-trip.
        assert _cell_decimal(row2["gbp_tt"]) == Decimal("42.1234")
        assert _cell_decimal(row2["gbp_sell"]) == Decimal("43.5678")
        assert _cell_decimal(row2["jpy_tt"]) == Decimal("0.2155")
        assert _cell_decimal(row2["jpy_sell"]) == Decimal("0.2233")

        # ── Row 3 = 2025-03-11 (Tuesday) — GBP only, JPY missing ──────
        assert _cell_decimal(ws.cell(row=3, column=2).value) == Decimal("42.2200")
        assert _cell_decimal(ws.cell(row=3, column=3).value) == Decimal("43.6600")
        # JPY has no record for the 11th -> empty cells, not stale data.
        assert ws.cell(row=3, column=4).value is None
        assert ws.cell(row=3, column=5).value is None
        wb.close()

    def test_value_is_exact_4dp_and_formatted(
        self, exrate_file, temp_backup, tmp_cache,
    ):
        """The written value must equal the exact 4dp rate and carry the
        0.0000 number format (the 4dp presentation guarantee).

        Mathematical Truth is asserted as an EXACT Decimal value via
        Decimal(str(cell)) — never pytest.approx. (Type-level Decimal does not
        survive openpyxl's save/reload; see `issues`.)
        """
        dr = (date(2025, 3, 10), date(2025, 3, 10))
        rate_types = {"Buying TT": "buying_transfer"}
        per_ccy = {
            "GBP": [SimpleNamespace(
                period="2025-03-10", currency="GBP",
                buying_transfer=Decimal("42.1234"),
                selling=None, buying_sight=None, mid_rate=None,
            )],
        }
        _eng, out = self._run(
            exrate_file, temp_backup, tmp_cache, per_ccy,
            rate_types, ["GBP"], dr,
        )
        wb = openpyxl.load_workbook(out)
        ws = wb["ExRate"]
        cell = ws.cell(row=2, column=2)
        # Exact value (not an approximation):
        assert _cell_decimal(cell.value) == Decimal("42.1234")
        assert _cell_decimal(cell.value) != Decimal("42.1235")
        # 4dp presentation enforced via number_format.
        assert cell.number_format == "0.0000"
        wb.close()

    def test_raw_api_float_is_quantized_to_4dp(
        self, exrate_file, temp_backup, tmp_cache,
    ):
        """Production BOTRateDetail fields are floats with arbitrary precision.

        The custom path must apply the same safe_to_decimal 4dp quantization as
        the standard USD/EUR path — never persist the raw API float. Feeds a
        value with >4dp and asserts the stored value equals the 4dp-quantized
        rate (Mathematical Truth), not the raw float.
        """
        from core.logic import safe_to_decimal

        raw = 42.123456  # 6dp float, as the live API would return
        dr = (date(2025, 3, 10), date(2025, 3, 10))
        per_ccy = {
            "GBP": [SimpleNamespace(
                period="2025-03-10", currency="GBP",
                buying_transfer=raw,
                selling=None, buying_sight=None, mid_rate=None,
            )],
        }
        _eng, out = self._run(
            exrate_file, temp_backup, tmp_cache, per_ccy,
            {"Buying TT": "buying_transfer"}, ["GBP"], dr,
        )
        wb = openpyxl.load_workbook(out)
        ws = wb["ExRate"]
        cell_val = ws.cell(row=2, column=2).value
        # Quantized to exactly 4dp, matching the standard path's discipline.
        assert _cell_decimal(cell_val) == safe_to_decimal(raw)
        assert _cell_decimal(cell_val) != Decimal(str(raw))  # raw float NOT stored
        wb.close()

    def test_stale_content_cleared_and_rewritten(
        self, exrate_file, temp_backup, tmp_cache,
    ):
        # Confirm the seeded junk ("OLD-DATE", date(1999,1,1)) is gone.
        dr = (date(2025, 3, 10), date(2025, 3, 10))
        per_ccy = {
            "GBP": [SimpleNamespace(
                period="2025-03-10", currency="GBP",
                buying_transfer=Decimal("42.0000"),
                selling=None, buying_sight=None, mid_rate=None,
            )],
        }
        _eng, out = self._run(
            exrate_file, temp_backup, tmp_cache, per_ccy,
            {"Buying TT": "buying_transfer"}, ["GBP"], dr,
        )
        wb = openpyxl.load_workbook(out)
        ws = wb["ExRate"]
        all_values = {
            ws.cell(row=r, column=c).value
            for r in range(1, (ws.max_row or 1) + 1)
            for c in range(1, (ws.max_column or 1) + 1)
        }
        assert "OLD-DATE" not in all_values
        assert "OLD-USD" not in all_values
        assert date(1999, 1, 1) not in all_values
        # New header was written.
        assert ws.cell(row=1, column=1).value == "Date"
        wb.close()

    def test_disk_space_guard_blocks_overwrite(
        self, exrate_file, temp_backup, tmp_cache, monkeypatch,
    ):
        """OSError disk guard must fire BEFORE wb.save in the custom path.

        The original file content must remain intact (no partial overwrite).
        """
        # Snapshot the original on-disk content.
        with open(exrate_file, "rb") as fh:
            original_bytes = fh.read()

        # Force the disk-space check to report a tiny free space.
        monkeypatch.setattr(
            engine_mod.shutil, "disk_usage",
            lambda _path: _DiskUsage(total=10**12, used=10**12, free=0),
        )

        api = _make_api()
        api.get_exchange_rates = AsyncMock(
            side_effect=_make_currency_side_effect({
                "GBP": [SimpleNamespace(
                    period="2025-03-10", currency="GBP",
                    buying_transfer=Decimal("42.0000"),
                    selling=None, buying_sight=None, mid_rate=None,
                )],
            })
        )
        eng = LedgerEngine(api, backup=temp_backup, cache=tmp_cache)

        with pytest.raises(OSError, match="Insufficient disk space"):
            asyncio.run(eng.update_exrate_standalone(
                exrate_file,
                currencies=["GBP"],
                rate_types={"Buying TT": "buying_transfer"},
                date_range=(date(2025, 3, 10), date(2025, 3, 10)),
            ))

        # File on disk is byte-for-byte unchanged — the guard prevented save.
        with open(exrate_file, "rb") as fh:
            assert fh.read() == original_bytes
