#!/usr/bin/env python3
"""
core/database.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.6.1) - Zero-Latency Local Cache
---------------------------------------------------------------------------
Ultra-lightweight SQLite cache using only Python's built-in sqlite3.
Thread-safe via check_same_thread=False + threading.Lock() on all operations.
Zero external dependencies.

V2.6.1 Schema: Expanded rates table with Buying TT / Selling columns
for both USD and EUR (4 rate columns total).
"""

import os
import sqlite3
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


class CacheDB:
    """
    Thread-safe SQLite cache for BOT exchange rates and holidays.
    Persists to data/cache.db. Tables are auto-created on init.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            db_dir = os.path.join(project_root, "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "cache.db")

        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
        self._create_tables()
        self._migrate_schema()

    def _create_tables(self):
        """Safely create tables if they do not exist."""
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS rates (
                    date          TEXT PRIMARY KEY,
                    usd_buying    REAL,
                    usd_selling   REAL,
                    eur_buying    REAL,
                    eur_selling   REAL
                );

                CREATE TABLE IF NOT EXISTS holidays (
                    date           TEXT PRIMARY KEY,
                    holiday_name   TEXT
                );

                CREATE TABLE IF NOT EXISTS rates_multi (
                    date       TEXT NOT NULL,
                    currency   TEXT NOT NULL,
                    rate_type  TEXT NOT NULL,
                    value      REAL,
                    PRIMARY KEY (date, currency, rate_type)
                );
            """)
            self._conn.commit()

    def _migrate_schema(self):
        """
        Auto-migrate from old 2-column schema to new 4-column schema.
        If old columns (usd_rate, eur_rate) exist, add new columns and
        copy data: selling gets the old rate, buying also gets it as
        best-available fallback for historical data.
        """
        with self._lock:
            cursor = self._conn.execute("PRAGMA table_info(rates)")
            columns = [row[1] for row in cursor.fetchall()]

            if "usd_rate" in columns and "usd_buying" not in columns:
                # Old schema detected — migrate (add new columns)
                self._conn.executescript("""
                    ALTER TABLE rates ADD COLUMN usd_buying REAL;
                    ALTER TABLE rates ADD COLUMN usd_selling REAL;
                    ALTER TABLE rates ADD COLUMN eur_buying REAL;
                    ALTER TABLE rates ADD COLUMN eur_selling REAL;

                    UPDATE rates SET
                        usd_buying  = usd_rate,
                        usd_selling = usd_rate,
                        eur_buying  = eur_rate,
                        eur_selling = eur_rate;
                """)
                self._conn.commit()
            elif "usd_rate" in columns and "usd_buying" in columns:
                # Migration ran before but didn't backfill buying — fix it
                self._conn.execute("""
                    UPDATE rates SET
                        usd_buying  = COALESCE(usd_buying, usd_selling, usd_rate),
                        eur_buying  = COALESCE(eur_buying, eur_selling, eur_rate)
                    WHERE usd_buying IS NULL OR eur_buying IS NULL
                """)
                self._conn.commit()

    # ================================================================== #
    #  RATES
    # ================================================================== #
    def get_rate(self, target_date: date) -> Optional[Dict]:
        """
        Cache lookup for a single date's rates.

        Returns:
            Dict with keys: usd_buying, usd_selling, eur_buying, eur_selling
            or None if not cached.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT usd_buying, usd_selling, eur_buying, eur_selling FROM rates WHERE date = ?",
                (date_str,)
            ).fetchone()

        if row is None:
            return None

        return {
            "usd_buying": Decimal(str(row[0])) if row[0] is not None else None,
            "usd_selling": Decimal(str(row[1])) if row[1] is not None else None,
            "eur_buying": Decimal(str(row[2])) if row[2] is not None else None,
            "eur_selling": Decimal(str(row[3])) if row[3] is not None else None,
        }

    def get_rates_bulk(self, start: date, end: date) -> dict:
        """
        Returns all cached rates in a date range as:
        {date_obj: {"usd_buying": ..., "usd_selling": ..., "eur_buying": ..., "eur_selling": ...}}
        """
        s_str = start.strftime("%Y-%m-%d")
        e_str = end.strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, usd_buying, usd_selling, eur_buying, eur_selling "
                "FROM rates WHERE date BETWEEN ? AND ?",
                (s_str, e_str)
            ).fetchall()

        result = {}
        for r in rows:
            d = datetime.strptime(r[0], "%Y-%m-%d").date()
            result[d] = {
                "usd_buying": Decimal(str(r[1])) if r[1] is not None else None,
                "usd_selling": Decimal(str(r[2])) if r[2] is not None else None,
                "eur_buying": Decimal(str(r[3])) if r[3] is not None else None,
                "eur_selling": Decimal(str(r[4])) if r[4] is not None else None,
            }
        return result

    def insert_rate(self, target_date: date, usd_buying: float = None,
                    usd_selling: float = None, eur_buying: float = None,
                    eur_selling: float = None):
        """Insert or update a single rate entry."""
        date_str = target_date.strftime("%Y-%m-%d")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rates "
                "(date, usd_buying, usd_selling, eur_buying, eur_selling) "
                "VALUES (?, ?, ?, ?, ?)",
                (date_str, usd_buying, usd_selling, eur_buying, eur_selling)
            )
            self._conn.commit()

    def insert_rates_bulk(self, entries: List[Tuple]):
        """
        Bulk insert/update rates.
        Each entry is (date_str, usd_buying, usd_selling, eur_buying, eur_selling).
        """
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO rates "
                "(date, usd_buying, usd_selling, eur_buying, eur_selling) "
                "VALUES (?, ?, ?, ?, ?)",
                entries
            )
            self._conn.commit()

    # ================================================================== #
    #  HOLIDAYS
    # ================================================================== #
    def get_holidays(self, year: int = None) -> List[Tuple[str, str]]:
        """
        Returns cached holidays as [(date_str, name), ...].
        If year is specified, filters to that year.
        """
        with self._lock:
            if year:
                prefix = f"{year}-"
                rows = self._conn.execute(
                    "SELECT date, holiday_name FROM holidays WHERE date LIKE ?",
                    (prefix + "%",)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT date, holiday_name FROM holidays"
                ).fetchall()
        return rows

    def has_holidays_for_year(self, year: int) -> bool:
        """Quick check if holidays for a year are already cached."""
        prefix = f"{year}-"
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM holidays WHERE date LIKE ?",
                (prefix + "%",)
            ).fetchone()
        return row[0] > 0

    def insert_holidays(self, holidays: List[Tuple[str, str]]):
        """
        Bulk insert holidays. Each entry is (date_str, holiday_name).
        """
        if not holidays:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO holidays (date, holiday_name) VALUES (?, ?)",
                holidays
            )
            self._conn.commit()

    # ================================================================== #
    #  MULTI-CURRENCY RATES (v3.1.0)
    # ================================================================== #

    def get_multi_rate(
        self, target_date: date, currency: str, rate_type: str,
    ) -> Optional[Decimal]:
        """Get a single rate from the multi-currency table."""
        date_str = target_date.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM rates_multi "
                "WHERE date = ? AND currency = ? AND rate_type = ?",
                (date_str, currency, rate_type),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0]))

    def get_multi_rates_bulk(
        self, start: date, end: date, currency: str, rate_type: str,
    ) -> Dict[date, Decimal]:
        """Get all rates for a currency/type in a date range."""
        s_str = start.strftime("%Y-%m-%d")
        e_str = end.strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, value FROM rates_multi "
                "WHERE date BETWEEN ? AND ? AND currency = ? AND rate_type = ?",
                (s_str, e_str, currency, rate_type),
            ).fetchall()
        result: Dict[date, Decimal] = {}
        for r in rows:
            d = datetime.strptime(r[0], "%Y-%m-%d").date()
            if r[1] is not None:
                result[d] = Decimal(str(r[1]))
        return result

    def insert_multi_rates_bulk(
        self, entries: List[Tuple],
    ) -> None:
        """
        Bulk insert into the multi-currency rates table.
        Each entry is (date_str, currency, rate_type, value).
        """
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO rates_multi "
                "(date, currency, rate_type, value) "
                "VALUES (?, ?, ?, ?)",
                entries,
            )
            self._conn.commit()

    # ================================================================== #
    #  EXPORT HELPERS
    # ================================================================== #

    def get_all_rates(self) -> list:
        """
        Returns all cached rates as a list of tuples:
        [(date_str, usd_buying, usd_selling, eur_buying, eur_selling), ...]
        Ordered by date ascending. Used by csv_export.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT date, usd_buying, usd_selling, eur_buying, eur_selling "
                "FROM rates ORDER BY date ASC"
            ).fetchall()

    # ================================================================== #
    #  CLEANUP
    # ================================================================== #
    def close(self):
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def get_stats(self) -> dict:
        """Returns cache statistics for UI display."""
        with self._lock:
            rates_count = self._conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]
            hol_count = self._conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]
            try:
                multi_count = self._conn.execute(
                    "SELECT COUNT(*) FROM rates_multi"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                multi_count = 0
        size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "rates": rates_count,
            "rates_multi": multi_count,
            "holidays": hol_count,
            "size_kb": round(size_bytes / 1024, 1)
        }
