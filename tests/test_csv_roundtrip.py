#!/usr/bin/env python3
"""
tests/test_csv_roundtrip.py
---------------------------------------------------------------------------
End-to-end export -> import -> export identity tests.

A backup taken via csv_export must re-import losslessly (exact Decimals,
all currencies and rate types) and re-export byte-for-byte identically.
"""

from datetime import date
from decimal import Decimal

from core.csv_export import export_rates_csv
from core.csv_import import import_bot_csv
from core.database import CacheDB


def _seed(cache: CacheDB) -> None:
    """Seed a DB with USD/EUR buying_transfer+selling and a GBP/mid_rate."""
    cache.insert_multi_rates_bulk([
        ("2025-01-02", "USD", "buying_transfer", Decimal("34.5000")),
        ("2025-01-02", "USD", "selling", Decimal("35.1150")),
        ("2025-01-02", "EUR", "buying_transfer", Decimal("37.2000")),
        ("2025-01-02", "EUR", "selling", Decimal("37.6000")),
        ("2025-01-03", "GBP", "mid_rate", Decimal("44.1234")),
    ])


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def test_export_import_export_is_identical(tmp_path):
    src = CacheDB(db_path=str(tmp_path / "src.db"))
    _seed(src)

    export1 = str(tmp_path / "export1.csv")
    n1 = export_rates_csv(export1, src)
    assert n1 == 5
    src.close()

    # Fresh DB, import the export.
    fresh = CacheDB(db_path=str(tmp_path / "fresh.db"))
    imported = import_bot_csv(export1, fresh)
    assert imported == 5

    # Exact Decimal preservation across the round-trip.
    assert fresh.get_multi_rate(
        date(2025, 1, 2), "USD", "selling",
    ) == Decimal("35.1150")
    assert fresh.get_multi_rate(
        date(2025, 1, 2), "USD", "buying_transfer",
    ) == Decimal("34.5000")
    assert fresh.get_multi_rate(
        date(2025, 1, 2), "EUR", "selling",
    ) == Decimal("37.6000")
    gbp = fresh.get_multi_rate(date(2025, 1, 3), "GBP", "mid_rate")
    assert isinstance(gbp, Decimal)
    assert gbp == Decimal("44.1234")

    # Re-export and compare byte-for-byte.
    export2 = str(tmp_path / "export2.csv")
    n2 = export_rates_csv(export2, fresh)
    assert n2 == n1
    fresh.close()

    assert _read_bytes(export1) == _read_bytes(export2)


def test_roundtrip_preserves_all_decimals_exactly(tmp_path):
    src = CacheDB(db_path=str(tmp_path / "src.db"))
    _seed(src)
    expected = {
        (d, c, rt): v for (d, c, rt, v) in src.get_all_multi_rates()
    }
    export1 = str(tmp_path / "e.csv")
    export_rates_csv(export1, src)
    src.close()

    fresh = CacheDB(db_path=str(tmp_path / "fresh.db"))
    import_bot_csv(export1, fresh)
    got = {(d, c, rt): v for (d, c, rt, v) in fresh.get_all_multi_rates()}
    fresh.close()

    assert got == expected
    for v in got.values():
        assert isinstance(v, Decimal)
