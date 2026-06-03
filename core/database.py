#!/usr/bin/env python3
"""
core/database.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.6.1) - Zero-Latency Local Cache
---------------------------------------------------------------------------
Ultra-lightweight SQLite cache using only Python's built-in sqlite3.
Thread-safe via connection-per-thread (threading.local): each thread opens
its own sqlite3 connection so WAL mode can actually overlap readers and the
writer instead of serializing everything behind a single global lock.
Zero external dependencies.

V2.6.1 Schema: Expanded rates table with Buying TT / Selling columns
for both USD and EUR (4 rate columns total).
"""

import atexit
import contextlib
import os
import sqlite3
import threading
import weakref
from datetime import date, datetime
from decimal import Decimal


class CacheDB:
    """
    Thread-safe SQLite cache for BOT exchange rates and holidays.
    Persists to data/cache.db. Tables are auto-created on init.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize the SQLite cache.

        Args:
            db_path: Path to the SQLite database file. Defaults to
                     data/cache.db in the project root.
        """
        if db_path is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            db_dir = os.path.join(project_root, "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "cache.db")

        self.db_path = db_path
        # Connection-per-thread: each thread gets its own sqlite3 connection
        # so WAL can overlap concurrent readers with the writer. A lock guards
        # the shared bookkeeping (the set of open connections) only.
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._all_conns: set = set()
        self._closed = False

        self._create_tables()
        self._migrate_schema()
        atexit.register(_atexit_close, weakref.ref(self))

    def _conn(self) -> sqlite3.Connection:
        """Return this thread's connection, creating it on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
            with self._conn_lock:
                self._all_conns.add(conn)
        return conn

    def _create_tables(self):
        """Safely create tables if they do not exist."""
        conn = self._conn()
        conn.executescript("""
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
                value      TEXT,
                PRIMARY KEY (date, currency, rate_type)
            );
        """)
        conn.commit()

    def _migrate_schema(self):
        """
        Auto-migrate from old 2-column schema to new 4-column schema.
        If old columns (usd_rate, eur_rate) exist, add new columns and
        copy data: selling gets the old rate, buying also gets it as
        best-available fallback for historical data.

        Each ALTER is guarded by an existence check (idempotent) rather than
        relying on an unguarded multi-statement executescript that cannot
        partially roll back.
        """
        conn = self._conn()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(rates)").fetchall()]

        if "usd_rate" in columns and "usd_buying" not in columns:
            # Old schema detected — add only the columns that are missing.
            for col in ("usd_buying", "usd_selling", "eur_buying", "eur_selling"):
                if col not in columns:
                    conn.execute(f"ALTER TABLE rates ADD COLUMN {col} REAL")
            conn.execute("""
                UPDATE rates SET
                    usd_buying  = usd_rate,
                    usd_selling = usd_rate,
                    eur_buying  = eur_rate,
                    eur_selling = eur_rate
            """)
            conn.commit()
        elif "usd_rate" in columns and "usd_buying" in columns:
            # Migration ran before but didn't backfill buying — fix it
            conn.execute("""
                UPDATE rates SET
                    usd_buying  = COALESCE(usd_buying, usd_selling, usd_rate),
                    eur_buying  = COALESCE(eur_buying, eur_selling, eur_rate)
                WHERE usd_buying IS NULL OR eur_buying IS NULL
            """)
            conn.commit()

        self._migrate_rates_multi_value_text(conn)

    def _migrate_rates_multi_value_text(self, conn: sqlite3.Connection) -> None:
        """
        Ensure rates_multi.value uses TEXT affinity so Decimal strings round-trip
        exactly. Older DBs created the column as REAL, which silently coerces
        Decimal strings back to lossy floats on insert. Recreate the table
        preserving existing rows when a legacy REAL column is detected.
        """
        info = conn.execute("PRAGMA table_info(rates_multi)").fetchall()
        value_decl = next(
            (row[2] for row in info if row[1] == "value"), None
        )
        if value_decl is None or value_decl.upper() == "TEXT":
            return

        conn.executescript("""
            CREATE TABLE rates_multi_new (
                date       TEXT NOT NULL,
                currency   TEXT NOT NULL,
                rate_type  TEXT NOT NULL,
                value      TEXT,
                PRIMARY KEY (date, currency, rate_type)
            );
            INSERT INTO rates_multi_new (date, currency, rate_type, value)
                SELECT date, currency, rate_type, value FROM rates_multi;
            DROP TABLE rates_multi;
            ALTER TABLE rates_multi_new RENAME TO rates_multi;
        """)
        conn.commit()

    # ================================================================== #
    #  RATES
    # ================================================================== #
    def get_rate(self, target_date: date) -> dict | None:
        """
        Cache lookup for a single date's rates.

        Returns:
            Dict with keys: usd_buying, usd_selling, eur_buying, eur_selling
            or None if not cached.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        row = self._conn().execute(
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
        rows = self._conn().execute(
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
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO rates "
            "(date, usd_buying, usd_selling, eur_buying, eur_selling) "
            "VALUES (?, ?, ?, ?, ?)",
            (date_str, usd_buying, usd_selling, eur_buying, eur_selling)
        )
        conn.commit()

    def insert_rates_bulk(self, entries: list[tuple]):
        """
        Bulk insert/update rates.
        Each entry is (date_str, usd_buying, usd_selling, eur_buying, eur_selling).
        """
        if not entries:
            return
        conn = self._conn()
        conn.executemany(
            "INSERT OR REPLACE INTO rates "
            "(date, usd_buying, usd_selling, eur_buying, eur_selling) "
            "VALUES (?, ?, ?, ?, ?)",
            entries
        )
        conn.commit()

    # ================================================================== #
    #  HOLIDAYS
    # ================================================================== #
    def get_holidays(self, year: int = None) -> list[tuple[str, str]]:
        """
        Returns cached holidays as [(date_str, name), ...].
        If year is specified, filters to that year.
        """
        conn = self._conn()
        if year:
            prefix = f"{year}-"
            rows = conn.execute(
                "SELECT date, holiday_name FROM holidays WHERE date LIKE ?",
                (prefix + "%",)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT date, holiday_name FROM holidays"
            ).fetchall()
        return rows

    def has_holidays_for_year(self, year: int) -> bool:
        """Quick check if holidays for a year are already cached."""
        prefix = f"{year}-"
        row = self._conn().execute(
            "SELECT COUNT(*) FROM holidays WHERE date LIKE ?",
            (prefix + "%",)
        ).fetchone()
        return row[0] > 0

    def insert_holidays(self, holidays: list[tuple[str, str]]):
        """
        Bulk insert holidays. Each entry is (date_str, holiday_name).
        """
        if not holidays:
            return
        conn = self._conn()
        conn.executemany(
            "INSERT OR REPLACE INTO holidays (date, holiday_name) VALUES (?, ?)",
            holidays
        )
        conn.commit()

    # ================================================================== #
    #  MULTI-CURRENCY RATES (v3.1.0)
    # ================================================================== #

    def get_multi_rate(
        self, target_date: date, currency: str, rate_type: str,
    ) -> Decimal | None:
        """Get a single rate from the multi-currency table."""
        date_str = target_date.strftime("%Y-%m-%d")
        row = self._conn().execute(
            "SELECT value FROM rates_multi "
            "WHERE date = ? AND currency = ? AND rate_type = ?",
            (date_str, currency, rate_type),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0]))


    def insert_multi_rates_bulk(
        self, entries: list[tuple],
    ) -> None:
        """
        Bulk insert into the multi-currency rates table.
        Each entry is (date_str, currency, rate_type, value).
        """
        if not entries:
            return
        # Store value as TEXT (str of Decimal) so 4dp digits round-trip exactly.
        # The rates_multi.value column has TEXT affinity; passing a string keeps
        # it verbatim instead of coercing through a lossy float.
        normalized = [
            (
                d,
                c,
                rt,
                None if v is None else str(v),
            )
            for (d, c, rt, v) in entries
        ]
        conn = self._conn()
        conn.executemany(
            "INSERT OR REPLACE INTO rates_multi "
            "(date, currency, rate_type, value) "
            "VALUES (?, ?, ?, ?)",
            normalized,
        )
        conn.commit()

    # ================================================================== #
    #  EXPORT HELPERS
    # ================================================================== #

    def get_all_rates(self) -> list:
        """
        Returns all cached rates as a list of tuples:
        [(date_str, usd_buying, usd_selling, eur_buying, eur_selling), ...]
        Ordered by date ascending. Used by csv_export.
        """
        return self._conn().execute(
            "SELECT date, usd_buying, usd_selling, eur_buying, eur_selling "
            "FROM rates ORDER BY date ASC"
        ).fetchall()

    def get_all_multi_rates(self) -> list[tuple[str, str, str, Decimal | None]]:
        """
        Returns every multi-currency rate as a list of tuples:
        [(date_str, currency, rate_type, value), ...]
        where value is an exact Decimal (or None). Ordered by
        date, currency, rate_type. Used by csv_export for lossless export.
        """
        rows = self._conn().execute(
            "SELECT date, currency, rate_type, value "
            "FROM rates_multi ORDER BY date ASC, currency ASC, rate_type ASC"
        ).fetchall()
        return [
            (
                r[0],
                r[1],
                r[2],
                Decimal(str(r[3])) if r[3] is not None else None,
            )
            for r in rows
        ]

    # ================================================================== #
    #  CLEANUP
    # ================================================================== #
    def close(self):
        """Checkpoint the WAL and close every per-thread connection."""
        with self._conn_lock:
            if self._closed:
                return
            self._closed = True
            conns = list(self._all_conns)
            self._all_conns.clear()

        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with contextlib.suppress(sqlite3.Error):
                conn.close()

        # Drop this thread's cached handle so a later call re-opens cleanly.
        if getattr(self._local, "conn", None) is not None:
            self._local.conn = None

    def __enter__(self) -> "CacheDB":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_stats(self) -> dict:
        """Returns cache statistics for UI display."""
        conn = self._conn()
        rates_count = conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]
        hol_count = conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]
        try:
            multi_count = conn.execute(
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


def _atexit_close(db_ref) -> None:
    """Module-level atexit handler: checkpoint + close the singleton cache."""
    db = db_ref()
    if db is not None:
        with contextlib.suppress(Exception):
            db.close()


# ===================================================================== #
#  PUBLIC PROCESS-SINGLETON ACCESSOR
# ===================================================================== #
_cache_singleton: CacheDB | None = None
_cache_singleton_lock = threading.Lock()


def get_cache() -> CacheDB:
    """Return the process-wide singleton :class:`CacheDB`.

    Lazily constructs a single ``CacheDB`` at the default db path
    (``data/cache.db``) on first call and returns that same instance on
    every subsequent call. Thread-safe via a double-checked lock.

    This is the public, package-boundary-stable accessor. GUI panels and
    other callers should import it from ``core.database`` rather than
    reaching into ``core.engine``'s private ``_get_cache``. ``core.engine``
    intentionally keeps its own private singleton; this one is independent
    and owns its own lifecycle (an ``atexit`` close is already registered by
    ``CacheDB.__init__``).
    """
    global _cache_singleton
    if _cache_singleton is None:
        with _cache_singleton_lock:
            if _cache_singleton is None:  # double-check after lock
                _cache_singleton = CacheDB()
    return _cache_singleton
