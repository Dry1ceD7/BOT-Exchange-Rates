#!/usr/bin/env python3
"""
tests/test_database.py
---------------------------------------------------------------------------
Unit tests for core/database.py — CacheDB thread-safe SQLite operations.
V2.5.4: Updated to 4-column rate schema (usd_buying, usd_selling,
        eur_buying, eur_selling).
---------------------------------------------------------------------------
"""

import os
import tempfile
import threading
from datetime import date
from decimal import Decimal

import pytest

from core.database import CacheDB


@pytest.fixture
def db():
    """Creates a temporary CacheDB instance for each test."""
    tmp = tempfile.mktemp(suffix=".db")
    cache = CacheDB(db_path=tmp)
    yield cache
    cache.close()
    if os.path.exists(tmp):
        os.remove(tmp)
    # Clean up WAL/SHM files
    for ext in ("-wal", "-shm"):
        p = tmp + ext
        if os.path.exists(p):
            os.remove(p)


# =========================================================================
#  RATES (4-column schema)
# =========================================================================

class TestRates:
    """Tests for rate CRUD operations with 4-column schema."""

    def test_insert_and_get_single_rate(self, db):
        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=33.4, usd_selling=33.5,
                       eur_buying=36.1, eur_selling=36.2)
        result = db.get_rate(d)
        assert result is not None
        assert result["usd_buying"] == Decimal("33.4")
        assert result["usd_selling"] == Decimal("33.5")
        assert result["eur_buying"] == Decimal("36.1")
        assert result["eur_selling"] == Decimal("36.2")

    def test_get_rate_returns_none_for_missing(self, db):
        result = db.get_rate(date(2025, 1, 1))
        assert result is None

    def test_insert_rate_upsert(self, db):
        """INSERT OR REPLACE should update existing entries."""
        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=33.0, usd_selling=33.1)
        db.insert_rate(d, usd_buying=34.0, usd_selling=34.1,
                       eur_buying=37.0, eur_selling=37.1)
        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("34.0")
        assert result["usd_selling"] == Decimal("34.1")
        assert result["eur_buying"] == Decimal("37.0")
        assert result["eur_selling"] == Decimal("37.1")

    def test_insert_rate_with_none_values(self, db):
        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=33.4, usd_selling=33.5,
                       eur_buying=None, eur_selling=None)
        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("33.4")
        assert result["usd_selling"] == Decimal("33.5")
        assert result["eur_buying"] is None
        assert result["eur_selling"] is None

    def test_bulk_insert_and_retrieve(self, db):
        entries = [
            ("2025-03-10", 33.4, 33.5, 36.1, 36.2),
            ("2025-03-11", 33.5, 33.6, 36.2, 36.3),
            ("2025-03-12", 33.6, 33.7, 36.3, 36.4),
        ]
        db.insert_rates_bulk(entries)
        result = db.get_rates_bulk(date(2025, 3, 10), date(2025, 3, 12))
        assert len(result) == 3
        row = result[date(2025, 3, 11)]
        assert row["usd_buying"] == Decimal("33.5")
        assert row["usd_selling"] == Decimal("33.6")
        assert row["eur_buying"] == Decimal("36.2")
        assert row["eur_selling"] == Decimal("36.3")

    def test_bulk_insert_empty_list(self, db):
        db.insert_rates_bulk([])  # Should not raise


# =========================================================================
#  HOLIDAYS
# =========================================================================

class TestHolidays:
    """Tests for holiday CRUD operations."""

    def test_insert_and_get_holidays(self, db):
        holidays = [
            ("2025-01-01", "New Year's Day"),
            ("2025-04-13", "Songkran"),
        ]
        db.insert_holidays(holidays)
        result = db.get_holidays(year=2025)
        assert len(result) == 2

    def test_has_holidays_for_year(self, db):
        assert db.has_holidays_for_year(2025) is False
        db.insert_holidays([("2025-01-01", "New Year")])
        assert db.has_holidays_for_year(2025) is True
        assert db.has_holidays_for_year(2024) is False

    def test_get_holidays_all_years(self, db):
        db.insert_holidays([
            ("2024-12-31", "NYE 2024"),
            ("2025-01-01", "NY 2025"),
        ])
        result = db.get_holidays()
        assert len(result) == 2

    def test_insert_holidays_empty(self, db):
        db.insert_holidays([])  # Should not raise


# =========================================================================
#  STATS
# =========================================================================

class TestStats:
    """Tests for the get_stats utility."""

    def test_empty_stats(self, db):
        stats = db.get_stats()
        assert stats["rates"] == 0
        assert stats["holidays"] == 0
        assert stats["size_kb"] >= 0

    def test_stats_after_inserts(self, db):
        db.insert_rate(date(2025, 1, 1), usd_buying=33.0, usd_selling=33.1,
                       eur_buying=36.0, eur_selling=36.1)
        db.insert_holidays([("2025-01-01", "NY")])
        stats = db.get_stats()
        assert stats["rates"] == 1
        assert stats["holidays"] == 1


# =========================================================================
#  THREAD SAFETY
# =========================================================================

class TestThreadSafety:
    """Tests for concurrent read/write operations."""

    def test_concurrent_writes(self, db):
        """Multiple threads writing simultaneously should not corrupt data."""
        errors = []

        def writer(start_day: int):
            try:
                for i in range(10):
                    d = date(2025, 3, start_day)
                    db.insert_rate(d, usd_buying=33.0 + i * 0.01,
                                   usd_selling=33.1 + i * 0.01)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(d,)) for d in range(1, 11)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

    def test_concurrent_read_write(self, db):
        """Reading while writing should not raise."""
        db.insert_rate(date(2025, 1, 1), usd_buying=33.0, usd_selling=33.1,
                       eur_buying=36.0, eur_selling=36.1)
        errors = []

        def reader():
            try:
                for _ in range(50):
                    db.get_rate(date(2025, 1, 1))
            except Exception as e:
                errors.append(str(e))

        def writer():
            try:
                for i in range(50):
                    db.insert_rate(date(2025, 1, 1),
                                   usd_buying=33.0 + i * 0.001,
                                   usd_selling=33.1 + i * 0.001)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
