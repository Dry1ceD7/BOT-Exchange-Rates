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


def test_overprecise_external_csv_quantized_then_roundtrips(tmp_path):
    """F30: an external CSV carrying >4dp digits imports as exact 4dp
    Decimals (the Layer-1 hard gate), and the subsequent export -> import ->
    export cycle is byte-identical — quantization is idempotent after the
    first import, so the 4dp lossless guarantee is preserved."""
    external = str(tmp_path / "external.csv")
    with open(external, "w", encoding="utf-8") as f:
        f.write(
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-01-02,USD,buying_transfer,42.123456\n"
            "2025-01-02,USD,selling,34.800049\n"
        )

    db1 = CacheDB(db_path=str(tmp_path / "db1.db"))
    assert import_bot_csv(external, db1) == 2

    # Cache holds exactly the 4dp-quantized Decimals, at exactly 4dp.
    buy = db1.get_multi_rate(date(2025, 1, 2), "USD", "buying_transfer")
    assert buy == Decimal("42.1235")
    assert buy.as_tuple().exponent == -4
    sell = db1.get_multi_rate(date(2025, 1, 2), "USD", "selling")
    assert sell == Decimal("34.8000")
    for _, _, _, v in db1.get_all_multi_rates():
        assert v is not None and v.as_tuple().exponent == -4

    export1 = str(tmp_path / "export1.csv")
    assert export_rates_csv(export1, db1) == 2
    db1.close()

    db2 = CacheDB(db_path=str(tmp_path / "db2.db"))
    assert import_bot_csv(export1, db2) == 2
    assert db2.get_multi_rate(
        date(2025, 1, 2), "USD", "buying_transfer",
    ) == Decimal("42.1235")

    export2 = str(tmp_path / "export2.csv")
    assert export_rates_csv(export2, db2) == 2
    db2.close()

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
