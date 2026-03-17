#!/usr/bin/env python3
"""
core/database.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.3.1) - Zero-Latency Local Cache
---------------------------------------------------------------------------
Ultra-lightweight SQLite cache using only Python's built-in sqlite3.
Thread-safe via check_same_thread=False + threading.Lock() on writes.
Zero external dependencies.
"""

import os
import sqlite3
import threading
from datetime import date, datetime
from typing import Optional, Tuple, List
from decimal import Decimal


class CacheDB:
    """
    Thread-safe SQLite cache for BOT exchange rates and holidays.
    Persists to data/cache.db. Tables are auto-created on init.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_dir = os.path.join(project_root, "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "cache.db")

        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
        self._create_tables()

    def _create_tables(self):
        """Safely create tables if they do not exist."""
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS rates (
                    date       TEXT PRIMARY KEY,
                    usd_rate   REAL,
                    eur_rate   REAL
                );

                CREATE TABLE IF NOT EXISTS holidays (
                    date           TEXT PRIMARY KEY,
                    holiday_name   TEXT
                );
            """)
            self._conn.commit()

    # ================================================================== #
    #  RATES
    # ================================================================== #
    def get_rate(self, target_date: date) -> Optional[Tuple[Optional[Decimal], Optional[Decimal]]]:
        """
        Cache lookup for a single date's rates.

        Returns:
            (usd_rate, eur_rate) as Decimals, or None if not cached.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT usd_rate, eur_rate FROM rates WHERE date = ?", (date_str,)
        ).fetchone()

        if row is None:
            return None

        usd = Decimal(str(row[0])) if row[0] is not None else None
        eur = Decimal(str(row[1])) if row[1] is not None else None
        return (usd, eur)

    def get_rates_bulk(self, start: date, end: date) -> dict:
        """
        Returns all cached rates in a date range as {date_obj: (usd, eur)}.
        """
        s_str = start.strftime("%Y-%m-%d")
        e_str = end.strftime("%Y-%m-%d")
        rows = self._conn.execute(
            "SELECT date, usd_rate, eur_rate FROM rates WHERE date BETWEEN ? AND ?",
            (s_str, e_str)
        ).fetchall()

        result = {}
        for r in rows:
            d = datetime.strptime(r[0], "%Y-%m-%d").date()
            usd = Decimal(str(r[1])) if r[1] is not None else None
            eur = Decimal(str(r[2])) if r[2] is not None else None
            result[d] = (usd, eur)
        return result

    def insert_rate(self, target_date: date, usd_rate: float = None, eur_rate: float = None):
        """Insert or update a single rate entry."""
        date_str = target_date.strftime("%Y-%m-%d")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO rates (date, usd_rate, eur_rate) VALUES (?, ?, ?)",
                (date_str, usd_rate, eur_rate)
            )
            self._conn.commit()

    def insert_rates_bulk(self, entries: List[Tuple[str, Optional[float], Optional[float]]]):
        """
        Bulk insert/update rates. Each entry is (date_str, usd_rate, eur_rate).
        """
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO rates (date, usd_rate, eur_rate) VALUES (?, ?, ?)",
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
    #  CLEANUP
    # ================================================================== #
    def close(self):
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def get_stats(self) -> dict:
        """Returns cache statistics for UI display."""
        rates_count = self._conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]
        hol_count = self._conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]
        size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "rates": rates_count,
            "holidays": hol_count,
            "size_kb": round(size_bytes / 1024, 1)
        }
