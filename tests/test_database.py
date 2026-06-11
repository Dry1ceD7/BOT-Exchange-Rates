#!/usr/bin/env python3
"""
tests/test_database.py
---------------------------------------------------------------------------
Unit tests for core/database.py — CacheDB thread-safe SQLite operations.
V2.6.1: Updated to 4-column rate schema (usd_buying, usd_selling,
        eur_buying, eur_selling).
---------------------------------------------------------------------------
"""

import logging
import os
import threading
from datetime import date
from decimal import Decimal

import pytest

import core.database as database
from core.database import CacheDB, get_cache


@pytest.fixture
def db(tmp_path):
    """Creates a temporary CacheDB instance for each test."""
    tmp = str(tmp_path / "cache.db")
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
        """The upsert must update existing entries with non-NULL values."""
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
#  PARTIAL UPSERT NEVER NULLS SIBLING COLUMNS (F1 regression)
# =========================================================================

class TestPartialUpsertPreservesSiblings:
    """F1 regression: per-currency inserts must not wipe the other currency.

    A wide BOT CSV interleaves one USD row and one EUR row per date, and
    csv_import mirrors each via a per-currency insert_rate call. The old
    INSERT OR REPLACE rewrote the whole row, so the SECOND call nulled the
    first currency's columns — leaving only the LAST currency per date.
    """

    def test_usd_then_eur_insert_keeps_all_four_columns(self, db):
        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=34.0512, usd_selling=34.3209)
        db.insert_rate(d, eur_buying=37.1023, eur_selling=37.5541)

        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("34.0512")
        assert result["usd_selling"] == Decimal("34.3209")
        assert result["eur_buying"] == Decimal("37.1023")
        assert result["eur_selling"] == Decimal("37.5541")

    def test_eur_then_usd_insert_keeps_all_four_columns(self, db):
        """Order-independent: the last write must not win the whole row."""
        d = date(2025, 3, 11)
        db.insert_rate(d, eur_buying=37.1023, eur_selling=37.5541)
        db.insert_rate(d, usd_buying=34.0512, usd_selling=34.3209)

        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("34.0512")
        assert result["usd_selling"] == Decimal("34.3209")
        assert result["eur_buying"] == Decimal("37.1023")
        assert result["eur_selling"] == Decimal("37.5541")

    def test_non_null_update_still_overwrites(self, db):
        """COALESCE must not freeze stale values: a non-NULL new value wins
        (today's rate refreshes when new data arrives — Core Rule 5)."""
        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=34.0512, usd_selling=34.3209)
        db.insert_rate(d, usd_buying=34.1020)

        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("34.1020")
        assert result["usd_selling"] == Decimal("34.3209")

    def test_bulk_partial_entry_preserves_existing_columns(self, db):
        """insert_rates_bulk (the engine's API write-back path) honors the
        same per-column upsert: a row with NULL EUR slots must not erase the
        cached EUR values."""
        d = date(2025, 3, 10)
        db.insert_rate(d, eur_buying=37.1023, eur_selling=37.5541)
        db.insert_rates_bulk([("2025-03-10", 34.0512, 34.3209, None, None)])

        result = db.get_rate(d)
        assert result["usd_buying"] == Decimal("34.0512")
        assert result["usd_selling"] == Decimal("34.3209")
        assert result["eur_buying"] == Decimal("37.1023")
        assert result["eur_selling"] == Decimal("37.5541")


# =========================================================================
#  MULTI-CURRENCY RATES (lossless Decimal round-trip)
# =========================================================================

class TestMultiRates:
    """rates_multi must preserve exact Decimal values (TEXT affinity)."""

    def test_insert_and_get_rates_multi_exact(self, db):
        db.insert_multi_rates_bulk([
            ("2025-01-02", "USD", "buying_transfer", Decimal("35.1150")),
        ])
        rates = db.get_rates_multi(
            date(2025, 1, 2), date(2025, 1, 2), "USD", "buying_transfer",
        )
        rate = rates[date(2025, 1, 2)]
        assert isinstance(rate, Decimal)
        assert rate == Decimal("35.1150")

    def test_get_all_multi_rates_returns_exact_decimals(self, db):
        db.insert_multi_rates_bulk([
            ("2025-01-02", "GBP", "mid_rate", Decimal("44.1234")),
            ("2025-01-02", "USD", "selling", Decimal("35.0000")),
        ])
        rows = db.get_all_multi_rates()
        assert len(rows) == 2
        as_dict = {(d, c, rt): v for (d, c, rt, v) in rows}
        gbp = as_dict[("2025-01-02", "GBP", "mid_rate")]
        assert isinstance(gbp, Decimal)
        assert gbp == Decimal("44.1234")
        assert as_dict[("2025-01-02", "USD", "selling")] == Decimal("35.0000")

    def test_multi_rate_value_stored_as_text(self, db):
        """4dp digits must survive verbatim (no REAL coercion)."""
        db.insert_multi_rates_bulk([
            ("2025-01-02", "JPY", "mid_rate", Decimal("0.2300")),
        ])
        raw = db._conn().execute(
            "SELECT value, typeof(value) FROM rates_multi"
        ).fetchone()
        assert raw[0] == "0.2300"
        assert raw[1] == "text"

    def test_get_rates_multi_range_exact(self, db):
        """get_rates_multi returns {date: Decimal} for one (ccy, rate_type)."""
        db.insert_multi_rates_bulk([
            ("2025-01-02", "GBP", "buying_transfer", Decimal("44.1234")),
            ("2025-01-03", "GBP", "buying_transfer", Decimal("44.5678")),
            ("2025-01-04", "GBP", "buying_transfer", Decimal("44.9999")),
        ])
        rates = db.get_rates_multi(
            date(2025, 1, 2), date(2025, 1, 3), "GBP", "buying_transfer",
        )
        assert set(rates.keys()) == {date(2025, 1, 2), date(2025, 1, 3)}
        assert isinstance(rates[date(2025, 1, 2)], Decimal)
        assert rates[date(2025, 1, 2)] == Decimal("44.1234")
        assert rates[date(2025, 1, 3)] == Decimal("44.5678")

    def test_get_rates_multi_filters_currency_and_type(self, db):
        """Only the requested currency AND rate_type rows are returned."""
        db.insert_multi_rates_bulk([
            ("2025-01-02", "GBP", "buying_transfer", Decimal("44.0000")),
            ("2025-01-02", "GBP", "selling", Decimal("45.0000")),
            ("2025-01-02", "JPY", "buying_transfer", Decimal("0.2300")),
        ])
        gbp_buy = db.get_rates_multi(
            date(2025, 1, 1), date(2025, 1, 31), "GBP", "buying_transfer",
        )
        assert gbp_buy == {date(2025, 1, 2): Decimal("44.0000")}

    def test_get_rates_multi_empty_when_absent(self, db):
        """No rows for the (ccy, type, range) → empty dict, never None."""
        result = db.get_rates_multi(
            date(2025, 1, 1), date(2025, 1, 31), "GBP", "buying_transfer",
        )
        assert result == {}

    def test_get_rates_multi_skips_null_values(self, db):
        """A NULL stored value is omitted so only usable rates come back."""
        db.insert_multi_rates_bulk([
            ("2025-01-02", "GBP", "buying_transfer", None),
            ("2025-01-03", "GBP", "buying_transfer", Decimal("44.5000")),
        ])
        result = db.get_rates_multi(
            date(2025, 1, 1), date(2025, 1, 31), "GBP", "buying_transfer",
        )
        assert result == {date(2025, 1, 3): Decimal("44.5000")}


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


# =========================================================================
#  CONNECTION-PER-THREAD (fix #4)
# =========================================================================

class TestConnectionPerThread:
    """Each thread should get its own sqlite3 connection."""

    def test_distinct_connections_per_thread(self, db):
        ids = {}

        def grab(tag):
            ids[tag] = id(db._conn())

        main_conn = id(db._conn())
        threads = [threading.Thread(target=grab, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each worker thread's connection differs from the main thread's.
        assert all(cid != main_conn for cid in ids.values())
        # Same thread reuses its connection.
        assert id(db._conn()) == main_conn

    def test_write_visible_across_threads(self, db):
        """A write committed on one thread is readable on another (WAL)."""
        d = date(2025, 5, 1)
        db.insert_rate(d, usd_buying=30.0, usd_selling=30.1)

        result = {}

        def reader():
            result["row"] = db.get_rate(d)

        t = threading.Thread(target=reader)
        t.start()
        t.join()

        assert result["row"] is not None
        assert result["row"]["usd_buying"] == Decimal("30.0")


# =========================================================================
#  LIFECYCLE: close / atexit / context manager (fix #5)
# =========================================================================

class TestLifecycle:
    """Tests for close(), context-manager, and WAL checkpoint."""

    def test_context_manager_closes(self, tmp_path):
        tmp = str(tmp_path / "ctx.db")
        with CacheDB(db_path=tmp) as cache:
            cache.insert_rate(date(2025, 1, 1), usd_buying=1.0, usd_selling=1.1)
        # After exit, the connection set is empty (closed).
        assert cache._closed is True

    def test_close_is_idempotent(self, tmp_path):
        tmp = str(tmp_path / "idem.db")
        cache = CacheDB(db_path=tmp)
        cache.close()
        cache.close()  # second close must not raise
        assert cache._closed is True

    def test_close_truncates_wal(self, tmp_path):
        """After close, WAL checkpoint(TRUNCATE) should shrink the -wal file."""
        tmp = str(tmp_path / "wal.db")
        cache = CacheDB(db_path=tmp)
        cache.insert_rates_bulk([
            (f"2025-06-{d:02d}", 33.0, 33.1, 36.0, 36.1) for d in range(1, 28)
        ])
        cache.close()
        wal = tmp + "-wal"
        # WAL is either removed or truncated to zero bytes after checkpoint.
        assert (not os.path.exists(wal)) or os.path.getsize(wal) == 0


# =========================================================================
#  SCHEMA MIGRATION (fix #9)
# =========================================================================

class TestSchemaMigration:
    """Idempotent guarded migration from the old 2-column schema."""

    def test_migrates_old_schema_leaves_buying_null(self, tmp_path):
        """F32: the legacy single rate maps to the SELLING columns only.

        The old schema never recorded which rate type its single value was,
        so fabricating Buying TT from it wrote unauthentic values. The
        unknown Buying TT columns stay NULL — a per-column cache miss the
        engine self-heals with authentic API data.
        """
        import sqlite3
        tmp = str(tmp_path / "legacy.db")
        # Build an OLD-schema DB by hand.
        raw = sqlite3.connect(tmp)
        raw.execute(
            "CREATE TABLE rates (date TEXT PRIMARY KEY, usd_rate REAL, eur_rate REAL)"
        )
        raw.execute(
            "INSERT INTO rates (date, usd_rate, eur_rate) VALUES ('2025-01-02', 33.5, 36.5)"
        )
        raw.commit()
        raw.close()

        # Opening via CacheDB should migrate without error.
        cache = CacheDB(db_path=tmp)
        row = cache.get_rate(date(2025, 1, 2))
        assert row["usd_selling"] == Decimal("33.5")
        assert row["eur_selling"] == Decimal("36.5")
        # F32: Buying TT is unknown — never fabricated from the legacy rate.
        assert row["usd_buying"] is None
        assert row["eur_buying"] is None
        cache.close()

        # Re-opening (migration runs again) must be idempotent.
        cache2 = CacheDB(db_path=tmp)
        row2 = cache2.get_rate(date(2025, 1, 2))
        assert row2["usd_selling"] == Decimal("33.5")
        assert row2["usd_buying"] is None
        cache2.close()


# =========================================================================
#  RATES TABLE TEXT AFFINITY + READ-BOUNDARY EXACTNESS (Layer-1 hard gate)
# =========================================================================

class TestRatesTextAffinityAndExactness:
    """The legacy ``rates`` table must store TEXT and read exact 4dp Decimals.

    Mirrors the rates_multi REAL→TEXT migration: older cache.db files created
    the four rate columns as REAL (lossy float coercion); they are rebuilt
    in place to TEXT. Regardless of what legacy junk is stored, the read
    boundary (get_rate/get_rates_bulk) returns exact 4dp Decimals via
    safe_to_decimal.
    """

    _RATE_COLS = ("usd_buying", "usd_selling", "eur_buying", "eur_selling")

    def _col_decls(self, cache):
        info = cache._conn().execute("PRAGMA table_info(rates)").fetchall()
        return {row[1]: (row[2] or "").upper() for row in info}

    def test_fresh_db_creates_text_columns(self, db):
        decls = self._col_decls(db)
        for col in self._RATE_COLS:
            assert decls[col] == "TEXT"

    def test_insert_rate_stores_decimal_string_verbatim(self, db):
        db.insert_rate(date(2025, 3, 10), usd_buying=Decimal("34.5050"))
        raw = db._conn().execute(
            "SELECT usd_buying, typeof(usd_buying) FROM rates"
        ).fetchone()
        assert raw == ("34.5050", "text")

    def test_insert_rates_bulk_stores_text(self, db):
        db.insert_rates_bulk([
            ("2025-03-10", Decimal("34.5050"), 33.1, None, None),
        ])
        raw = db._conn().execute(
            "SELECT usd_buying, typeof(usd_buying), "
            "usd_selling, typeof(usd_selling) FROM rates"
        ).fetchone()
        assert raw == ("34.5050", "text", "33.1", "text")

    def test_legacy_real_schema_migrates_to_text(self, tmp_path):
        """A v3.5.x cache.db (4 REAL columns) is rebuilt in place to TEXT,
        preserving rows; reads come back as exact 4dp Decimals."""
        import sqlite3
        tmp = str(tmp_path / "real_rates.db")
        raw = sqlite3.connect(tmp)
        raw.execute(
            "CREATE TABLE rates (date TEXT PRIMARY KEY, usd_buying REAL, "
            "usd_selling REAL, eur_buying REAL, eur_selling REAL)"
        )
        # Float contamination: more than 4dp of REAL junk.
        raw.execute(
            "INSERT INTO rates VALUES "
            "('2025-01-02', 34.123456789, 34.567891, 37.123456, 37.654321)"
        )
        raw.commit()
        raw.close()

        cache = CacheDB(db_path=tmp)
        try:
            decls = self._col_decls(cache)
            for col in self._RATE_COLS:
                assert decls[col] == "TEXT"
            row = cache.get_rate(date(2025, 1, 2))
            assert row["usd_buying"] == Decimal("34.1235")
            assert row["usd_selling"] == Decimal("34.5679")
            assert row["eur_buying"] == Decimal("37.1235")
            assert row["eur_selling"] == Decimal("37.6543")
        finally:
            cache.close()

        # Re-opening (migration check runs again) must be idempotent.
        cache2 = CacheDB(db_path=tmp)
        try:
            assert cache2.get_rate(date(2025, 1, 2))["usd_buying"] == (
                Decimal("34.1235")
            )
        finally:
            cache2.close()

    def test_read_boundary_quantizes_to_exact_4dp(self, db):
        """Whatever junk is stored, every read is an exact 4dp Decimal."""
        db._conn().execute(
            "INSERT INTO rates (date, usd_buying, usd_selling) "
            "VALUES ('2025-03-10', '34.12345678', '33.1')"
        )
        db._conn().commit()
        row = db.get_rate(date(2025, 3, 10))
        assert row["usd_buying"] == Decimal("34.1235")
        assert row["usd_buying"].as_tuple().exponent == -4
        assert row["usd_selling"] == Decimal("33.1000")
        assert row["usd_selling"].as_tuple().exponent == -4

        bulk = db.get_rates_bulk(date(2025, 3, 10), date(2025, 3, 10))
        b_row = bulk[date(2025, 3, 10)]
        assert b_row["usd_buying"] == Decimal("34.1235")
        assert b_row["usd_buying"].as_tuple().exponent == -4

    def test_read_boundary_maps_unparseable_junk_to_none(self, db):
        """Garbage text (corrupt legacy data) reads as None — a per-column
        miss the engine re-fetches — instead of crashing or leaking junk."""
        db._conn().execute(
            "INSERT INTO rates (date, usd_buying, usd_selling) "
            "VALUES ('2025-03-11', 'garbage', '33.1')"
        )
        db._conn().commit()
        row = db.get_rate(date(2025, 3, 11))
        assert row["usd_buying"] is None
        assert row["usd_selling"] == Decimal("33.1000")


# =========================================================================
#  PUBLIC SINGLETON ACCESSOR: get_cache()
# =========================================================================

class TestGetCache:
    """core.database.get_cache() is the public process-singleton accessor."""

    @pytest.fixture
    def reset_singleton(self, tmp_path, monkeypatch):
        """Point the singleton at a temp DB and reset it before/after."""
        tmp_db = str(tmp_path / "singleton_cache.db")

        def _factory():
            return CacheDB(db_path=tmp_db)

        monkeypatch.setattr(database, "CacheDB", _factory)
        # Ensure a clean slate so each test builds its own instance.
        monkeypatch.setattr(database, "_cache_singleton", None)
        yield
        existing = getattr(database, "_cache_singleton", None)
        if existing is not None:
            existing.close()
            database._cache_singleton = None

    def test_returns_cachedb_instance(self, reset_singleton):
        cache = get_cache()
        assert isinstance(cache, CacheDB)

    def test_returns_same_singleton(self, reset_singleton):
        first = get_cache()
        second = get_cache()
        assert first is second

    def test_singleton_is_usable(self, reset_singleton):
        cache = get_cache()
        cache.insert_rate(
            date(2025, 7, 1), usd_buying=33.0, usd_selling=33.1
        )
        row = get_cache().get_rate(date(2025, 7, 1))
        assert row["usd_buying"] == Decimal("33.0")

    def test_thread_safe_singleton(self, reset_singleton):
        """Concurrent first-callers all get the same instance."""
        instances = []
        barrier = threading.Barrier(5)

        def grab():
            barrier.wait()
            instances.append(get_cache())

        threads = [threading.Thread(target=grab) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(instances) == 5
        assert all(inst is instances[0] for inst in instances)


# =========================================================================
#  CORRUPTION RECOVERY (fix: malformed cache.db must not kill the engine)
# =========================================================================

class TestCorruptionRecovery:
    """A corrupted cache.db is rebuildable from the API — recover cold."""

    def test_garbage_db_recovers_and_is_usable(self, tmp_path, caplog):
        """Writing garbage to cache.db → CacheDB() succeeds and is usable."""
        tmp = str(tmp_path / "cache.db")
        # Lay down bytes that look like a SQLite header but are malformed,
        # so quick_check fails when the schema is touched.
        with open(tmp, "wb") as fh:
            fh.write(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 256)

        with caplog.at_level(logging.WARNING, logger="core.database"):
            cache = CacheDB(db_path=tmp)
        try:
            # Construction did not raise and the cache is fully functional.
            d = date(2025, 8, 1)
            cache.insert_rate(d, usd_buying=33.0, usd_selling=33.1)
            row = cache.get_rate(d)
            assert row["usd_buying"] == Decimal("33.0")
        finally:
            cache.close()

        # A clear warning was logged about the rebuild.
        assert any(
            r.levelno == logging.WARNING and "corrupt" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_recovery_unlinks_wal_shm_siblings(self, tmp_path):
        """Stale -wal/-shm siblings of a bad DB are removed on rebuild."""
        tmp = str(tmp_path / "cache.db")
        with open(tmp, "wb") as fh:
            fh.write(b"SQLite format 3\x00" + b"\x00garbage" * 200)
        # Leftover WAL/SHM from the crash.
        for suffix in ("-wal", "-shm"):
            with open(tmp + suffix, "wb") as fh:
                fh.write(b"stale")

        cache = CacheDB(db_path=tmp)
        try:
            # The fresh DB works and the stale siblings did not corrupt it.
            cache.insert_rate(date(2025, 8, 2), usd_buying=1.0, usd_selling=1.1)
            assert cache.get_rate(date(2025, 8, 2)) is not None
        finally:
            cache.close()


# =========================================================================
#  CLOSE CHECKPOINT (fix: WAL must truncate regardless of thread ownership)
# =========================================================================

class TestCloseCheckpoint:
    """close() must checkpoint even when connections were opened elsewhere."""

    def test_same_thread_close_checkpoints_wal(self, tmp_path):
        """No exception on close and the -wal is checkpointed/removed."""
        tmp = str(tmp_path / "same.db")
        cache = CacheDB(db_path=tmp)
        cache.insert_rates_bulk([
            (f"2025-09-{d:02d}", 33.0, 33.1, 36.0, 36.1) for d in range(1, 28)
        ])
        cache.close()  # must not raise
        wal = tmp + "-wal"
        assert (not os.path.exists(wal)) or os.path.getsize(wal) == 0

    def test_cross_thread_close_logs_not_silent(self, tmp_path, caplog):
        """A connection opened in a worker thread logs on cross-thread close."""
        tmp = str(tmp_path / "cross.db")
        cache = CacheDB(db_path=tmp)
        # Open a connection from a worker thread and write through it so the
        # connection is registered in _all_conns but owned by that thread.
        worker_done = threading.Event()

        def worker():
            cache.insert_rate(date(2025, 9, 1), usd_buying=5.0, usd_selling=5.1)
            worker_done.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert worker_done.is_set()

        # Closing from the MAIN thread cannot close the worker's connection;
        # that must be logged (debug), never silently suppressed.
        with caplog.at_level(logging.DEBUG, logger="core.database"):
            cache.close()  # must not raise

        assert any(
            r.levelno == logging.DEBUG and "thread" in r.getMessage().lower()
            for r in caplog.records
        ), "cross-thread close should log instead of silently passing"

        # The checkpoint still ran on a fresh connection: -wal is gone/empty.
        wal = tmp + "-wal"
        assert (not os.path.exists(wal)) or os.path.getsize(wal) == 0


# =========================================================================
#  FABRICATED BUYING TT CLEANUP (F32 residual — one-shot migration)
# =========================================================================

class TestFabricatedBuyingCleanup:
    """DBs migrated by OLDER builds copied the legacy single rate into BOTH
    the selling AND buying columns. The one-shot cleanup NULLs buying where
    it exactly equals selling (user_version 0 → 1); the NULLs self-heal via
    the per-column cache-miss refetch. Post-cleanup DBs are never touched
    again, so a genuine buying==selling survives later opens."""

    def _make_pre_cleanup_db(self, tmp_path, name="legacy_fab.db") -> str:
        """A 4-column DB as an OLDER build left it: fabricated buying values
        (buying == selling verbatim) and user_version still 0."""
        import sqlite3
        db_path = str(tmp_path / name)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE rates (
                date          TEXT PRIMARY KEY,
                usd_buying    TEXT,
                usd_selling   TEXT,
                eur_buying    TEXT,
                eur_selling   TEXT
            );
        """)
        # Fabricated row: both buying columns mirror selling exactly.
        conn.execute(
            "INSERT INTO rates VALUES "
            "('2025-03-10', '33.1000', '33.1000', '36.2000', '36.2000')"
        )
        # Authentic row: a real spread + an already-NULL eur_buying.
        conn.execute(
            "INSERT INTO rates VALUES "
            "('2025-03-11', '33.0500', '33.2000', NULL, '36.1000')"
        )
        conn.commit()
        conn.close()
        return db_path

    def test_equal_pairs_nulled_unequal_left_alone(self, tmp_path):
        db_path = self._make_pre_cleanup_db(tmp_path)
        db = CacheDB(db_path=db_path)
        try:
            fabricated = db.get_rate(date(2025, 3, 10))
            # buying == selling exactly → fabricated → NULLed (cache miss).
            assert fabricated["usd_buying"] is None
            assert fabricated["eur_buying"] is None
            # Selling values are untouched — they were always authentic.
            assert fabricated["usd_selling"] == Decimal("33.1000")
            assert fabricated["eur_selling"] == Decimal("36.2000")

            authentic = db.get_rate(date(2025, 3, 11))
            assert authentic["usd_buying"] == Decimal("33.0500")
            assert authentic["usd_selling"] == Decimal("33.2000")
            assert authentic["eur_buying"] is None
            assert authentic["eur_selling"] == Decimal("36.1000")
        finally:
            db.close()

    def test_cleanup_stamps_user_version_marker(self, tmp_path):
        db_path = self._make_pre_cleanup_db(tmp_path)
        db = CacheDB(db_path=db_path)
        try:
            version = db._conn().execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            assert version == database._FABRICATED_BUYING_CLEANUP_VERSION
        finally:
            db.close()

    def test_one_shot_skips_on_reopen(self, tmp_path):
        """After the marker is stamped, a genuine buying==selling pair
        inserted later survives every subsequent open untouched."""
        db_path = self._make_pre_cleanup_db(tmp_path)
        db = CacheDB(db_path=db_path)  # runs the cleanup, stamps version 1
        db.insert_rate(
            date(2025, 3, 12),
            usd_buying="34.0000", usd_selling="34.0000",
        )
        db.close()

        reopened = CacheDB(db_path=db_path)
        try:
            row = reopened.get_rate(date(2025, 3, 12))
            assert row["usd_buying"] == Decimal("34.0000")
            assert row["usd_selling"] == Decimal("34.0000")
        finally:
            reopened.close()

    def test_fresh_db_is_stamped_and_never_cleaned(self, tmp_path):
        """A brand-new DB has nothing to clean: it is stamped immediately,
        so authentic equal pairs cached later are never NULLed."""
        db_path = str(tmp_path / "fresh.db")
        db = CacheDB(db_path=db_path)
        version = db._conn().execute("PRAGMA user_version").fetchone()[0]
        assert version == database._FABRICATED_BUYING_CLEANUP_VERSION
        db.insert_rate(
            date(2025, 4, 1),
            eur_buying="36.5000", eur_selling="36.5000",
        )
        db.close()

        reopened = CacheDB(db_path=db_path)
        try:
            row = reopened.get_rate(date(2025, 4, 1))
            assert row["eur_buying"] == Decimal("36.5000")
        finally:
            reopened.close()


class TestRecoverFromCorruption:
    """A transient DatabaseError must never destroy a HEALTHY cache.db."""

    def test_transient_error_on_healthy_db_reraises_and_preserves(self, db):
        """quick_check says 'ok' → re-raise, keep the file.

        Regression: the old `raise exc` sat INSIDE a
        contextlib.suppress(sqlite3.DatabaseError); exc is itself a
        DatabaseError, so the suppress ate the re-raise and execution fell
        through to the unlink — a transient 'database is locked' (e.g. a
        concurrent GUI + scheduler instance) silently destroyed all cached
        rates, including CSV-imported offline rates that cannot be
        re-fetched from the API.
        """
        import sqlite3

        d = date(2025, 3, 10)
        db.insert_rate(d, usd_buying=33.4, usd_selling=33.5,
                       eur_buying=36.1, eur_selling=36.2)

        with pytest.raises(sqlite3.OperationalError):
            db._recover_from_corruption(
                sqlite3.OperationalError("database is locked"),
            )

        # The healthy DB survived with its data intact.
        result = db.get_rate(d)
        assert result is not None
        assert result["usd_buying"] == Decimal("33.4")
