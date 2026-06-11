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
import logging
import sqlite3
import threading
import weakref
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from core.logic import safe_to_decimal

logger = logging.getLogger(__name__)

# Per-column upsert for the legacy rates table. ON CONFLICT + COALESCE keeps
# any column the new row leaves as NULL — a USD-only insert (e.g. one row of
# a wide BOT CSV import) must never wipe the EUR columns the way the old
# INSERT OR REPLACE did (REPLACE rewrites the whole row, nulling unspecified
# columns). A non-NULL new value still overwrites, so today's rate refreshes
# when new data arrives (Core Rule 5).
_RATES_UPSERT_SQL = (
    "INSERT INTO rates "
    "(date, usd_buying, usd_selling, eur_buying, eur_selling) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(date) DO UPDATE SET "
    "usd_buying  = COALESCE(excluded.usd_buying,  usd_buying), "
    "usd_selling = COALESCE(excluded.usd_selling, usd_selling), "
    "eur_buying  = COALESCE(excluded.eur_buying,  eur_buying), "
    "eur_selling = COALESCE(excluded.eur_selling, eur_selling)"
)

# One-shot migration marker stored in ``PRAGMA user_version`` (0 on every DB
# created before this bookkeeping existed). Version 1 = the fabricated
# Buying TT cleanup (F32 residual) has run. Bump and chain new one-shot
# cleanups behind higher numbers if more are ever needed.
_FABRICATED_BUYING_CLEANUP_VERSION = 1


def _rate_text(value: float | str | Decimal | None) -> str | None:
    """Normalize a rate to its TEXT storage form (str), preserving None.

    Explicit Python-side stringification keeps the stored representation
    deterministic (e.g. str(Decimal('34.5050')) == '34.5050') instead of
    relying on SQLite's own numeric-to-text affinity conversion.
    """
    return None if value is None else str(value)


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
            db_dir = Path(project_root) / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            # Keep db_path as a str: sqlite3.connect and os.path consumers
            # below expect the same str behavior as before.
            db_path = str(db_dir / "cache.db")

        self.db_path = db_path
        # Connection-per-thread: each thread gets its own sqlite3 connection
        # so WAL can overlap concurrent readers with the writer. A lock guards
        # the shared bookkeeping (the set of open connections) only.
        self._local = threading.local()
        self._conn_lock = threading.Lock()
        self._all_conns: set = set()
        self._closed = False

        try:
            self._create_tables()
            self._migrate_schema()
        except sqlite3.DatabaseError as exc:
            # A corrupted cache.db (e.g. "database disk image is malformed"
            # after a power loss) must NOT kill the whole engine — the cache is
            # fully rebuildable from the API. Recover by recreating it cold.
            self._recover_from_corruption(exc)
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

    def _recover_from_corruption(self, exc: sqlite3.DatabaseError) -> None:
        """Recreate a corrupted cache.db from scratch so the engine keeps running.

        Confirms the damage with ``PRAGMA quick_check`` (a clean DB never
        reaches this path because :meth:`_create_tables` succeeds), then closes
        any open handles, unlinks ``cache.db`` plus its ``-wal``/``-shm``
        siblings, and re-opens a fresh empty DB. Processing continues
        cache-cold; missing rates are simply re-fetched from the API.
        """
        # quick_check on a fresh connection — best-effort; treat any failure
        # (including a second DatabaseError) as confirmation of corruption.
        with contextlib.suppress(sqlite3.DatabaseError):
            probe = sqlite3.connect(self.db_path)
            try:
                result = probe.execute("PRAGMA quick_check").fetchone()
                if result is not None and result[0] == "ok":
                    # quick_check disagrees — re-raise the original error rather
                    # than silently discard a DB that might be salvageable.
                    raise exc
            finally:
                probe.close()

        # Drop any handles we (or quick_check) may have opened on the bad file.
        with self._conn_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        self._local = threading.local()

        # Unlink the malformed DB and its WAL/SHM siblings.
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                Path(self.db_path + suffix).unlink(missing_ok=True)

        logger.warning(
            "Cache DB at %s was corrupted (%s); rebuilt empty. "
            "Rates will be re-fetched from the API.",
            self.db_path, exc,
        )

        # Re-open a clean DB and lay down the schema fresh.
        self._create_tables()
        self._migrate_schema()

    def _create_tables(self):
        """Safely create tables if they do not exist."""
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rates (
                date          TEXT PRIMARY KEY,
                usd_buying    TEXT,
                usd_selling   TEXT,
                eur_buying    TEXT,
                eur_selling   TEXT
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
        If old columns (usd_rate, eur_rate) exist, add the new columns and
        copy the legacy single rate into the SELLING columns only. The
        Buying TT columns are left NULL (F32): the legacy schema never
        recorded which rate type its single value was, so fabricating
        Buying TT from it wrote unauthentic values. A NULL column counts as
        a per-column cache miss, so authentic Buying TT values are
        re-fetched from the API on the next run.

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
                    conn.execute(f"ALTER TABLE rates ADD COLUMN {col} TEXT")
            conn.execute("""
                UPDATE rates SET
                    usd_selling = usd_rate,
                    eur_selling = eur_rate
            """)
            conn.commit()

        self._migrate_rates_value_text(conn)
        self._migrate_rates_multi_value_text(conn)
        self._cleanup_fabricated_buying(conn)

    def _migrate_rates_value_text(self, conn: sqlite3.Connection) -> None:
        """
        Ensure the four rate columns of the legacy ``rates`` table use TEXT
        affinity so exact 4dp Decimal strings round-trip verbatim. Older DBs
        created them as REAL, which coerces every stored rate through a
        lossy float. Recreate the table preserving existing rows when any
        legacy REAL column is detected (mirrors
        :meth:`_migrate_rates_multi_value_text`). Legacy float junk that
        survives the copy is normalized at the read boundary
        (get_rate/get_rates_bulk quantize via safe_to_decimal).
        """
        info = conn.execute("PRAGMA table_info(rates)").fetchall()
        decls = {row[1]: (row[2] or "") for row in info}
        rate_cols = ("usd_buying", "usd_selling", "eur_buying", "eur_selling")
        if not any(decls.get(col, "TEXT").upper() == "REAL" for col in rate_cols):
            return

        conn.executescript("""
            CREATE TABLE rates_new (
                date          TEXT PRIMARY KEY,
                usd_buying    TEXT,
                usd_selling   TEXT,
                eur_buying    TEXT,
                eur_selling   TEXT
            );
            INSERT INTO rates_new
                (date, usd_buying, usd_selling, eur_buying, eur_selling)
                SELECT date, usd_buying, usd_selling, eur_buying, eur_selling
                FROM rates;
            DROP TABLE rates;
            ALTER TABLE rates_new RENAME TO rates;
        """)
        conn.commit()

    def _cleanup_fabricated_buying(self, conn: sqlite3.Connection) -> None:
        """One-time cleanup of fabricated Buying TT values (F32 residual).

        DBs migrated by builds OLDER than the F32 fix copied the legacy
        schema's single rate into BOTH the selling AND the buying columns,
        fabricating Buying TT values the BOT never published. Detect those
        rows by exact equality (buying == selling, verbatim TEXT compare)
        and NULL the buying column: a NULL counts as a per-column cache
        miss, so the Wave-0 refetch self-heals it with the authentic API
        value on the next run.

        Rationale for exact-equality detection: BOT always publishes a
        buy/sell spread, so a genuine Buying TT exactly equal to Selling at
        4dp is practically nonexistent. Even a false positive costs only one
        per-column API refetch — never data loss.

        One-shot / idempotent: ``PRAGMA user_version`` (0 on every
        pre-cleanup DB; this project uses it for nothing else) is bumped to
        ``_FABRICATED_BUYING_CLEANUP_VERSION`` afterwards, so an authentic
        post-cleanup row where buying happens to equal selling is never
        touched on later opens. Fresh/empty DBs are stamped immediately
        (nothing to clean).
        """
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= _FABRICATED_BUYING_CLEANUP_VERSION:
            return

        cleaned = 0
        for buy_col, sell_col in (
            ("usd_buying", "usd_selling"),
            ("eur_buying", "eur_selling"),
        ):
            cursor = conn.execute(
                f"UPDATE rates SET {buy_col} = NULL "  # noqa: S608 — constant column names
                f"WHERE {buy_col} IS NOT NULL AND {buy_col} = {sell_col}"
            )
            cleaned += cursor.rowcount
        # PRAGMA does not support parameter binding; the value is a module
        # constant int, never user input.
        conn.execute(
            f"PRAGMA user_version = {_FABRICATED_BUYING_CLEANUP_VERSION}"
        )
        conn.commit()
        if cleaned:
            logger.info(
                "Cleared %d fabricated Buying TT value(s) left by an older "
                "build's schema migration; authentic rates will be "
                "re-fetched from the API on demand.",
                cleaned,
            )

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

        Read-boundary exactness gate: every value is rebuilt string-safe and
        quantized to 4dp via safe_to_decimal, so consumers receive an exact
        4dp Decimal regardless of any legacy float junk stored by older
        builds (pre-TEXT-affinity REAL rows). Unparseable junk maps to None
        (a per-column cache miss, self-healed by the API refetch).

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
            "usd_buying": safe_to_decimal(row[0]),
            "usd_selling": safe_to_decimal(row[1]),
            "eur_buying": safe_to_decimal(row[2]),
            "eur_selling": safe_to_decimal(row[3]),
        }

    def get_rates_bulk(self, start: date, end: date) -> dict:
        """
        Returns all cached rates in a date range as:
        {date_obj: {"usd_buying": ..., "usd_selling": ..., "eur_buying": ..., "eur_selling": ...}}

        Same read-boundary exactness gate as :meth:`get_rate`: every value is
        an exact 4dp Decimal via safe_to_decimal (legacy junk maps to None).
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
                "usd_buying": safe_to_decimal(r[1]),
                "usd_selling": safe_to_decimal(r[2]),
                "eur_buying": safe_to_decimal(r[3]),
                "eur_selling": safe_to_decimal(r[4]),
            }
        return result

    def insert_rate(self, target_date: date,
                    usd_buying: float | str | Decimal | None = None,
                    usd_selling: float | str | Decimal | None = None,
                    eur_buying: float | str | Decimal | None = None,
                    eur_selling: float | str | Decimal | None = None):
        """Insert or update a single rate entry.

        Values are stored as TEXT (str of the given value) so exact Decimal
        strings round-trip verbatim — never a lossy REAL coercion. The read
        boundary (get_rate/get_rates_bulk) quantizes to 4dp.

        Per-column upsert: columns passed as None preserve any value already
        cached for that date instead of nulling it (see _RATES_UPSERT_SQL).
        """
        date_str = target_date.strftime("%Y-%m-%d")
        conn = self._conn()
        conn.execute(
            _RATES_UPSERT_SQL,
            (date_str, _rate_text(usd_buying), _rate_text(usd_selling),
             _rate_text(eur_buying), _rate_text(eur_selling))
        )
        conn.commit()

    def insert_rates_bulk(self, entries: list[tuple]):
        """
        Bulk insert/update rates.
        Each entry is (date_str, usd_buying, usd_selling, eur_buying, eur_selling).
        Values are stored as TEXT (str of the given value) so exact Decimal
        strings round-trip verbatim (see insert_rate).
        Per-column upsert: None values preserve existing cached columns
        instead of nulling them (see _RATES_UPSERT_SQL).
        """
        if not entries:
            return
        normalized = [
            (d, _rate_text(ub), _rate_text(us), _rate_text(eb), _rate_text(es))
            for (d, ub, us, eb, es) in entries
        ]
        conn = self._conn()
        conn.executemany(_RATES_UPSERT_SQL, normalized)
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
        """Get a single rate from the multi-currency table.

        Thin exact-date wrapper over :meth:`get_rates_multi` (the single
        owner of the rates_multi read path) — a one-day range lookup.
        No runtime caller; retained as a convenience consumed by the CSV
        import/round-trip test suites.
        """
        return self.get_rates_multi(
            target_date, target_date, currency, rate_type,
        ).get(target_date)

    def get_rates_multi(
        self, start: date, end: date, currency: str, rate_type: str,
    ) -> dict[date, Decimal]:
        """Return every cached ``(currency, rate_type)`` rate in a date range.

        Returns ``{date: Decimal}`` (the same per-currency shape the engine's
        extra-currency fetch produces), so a cache-first ledger path can read
        CSV-imported GBP/JPY/etc. rates directly from ``rates_multi`` instead
        of reaching the API. Dates with a NULL stored value are omitted so the
        result only carries usable rates.

        Featherweight: one indexed range scan, exact-Decimal round-trip from
        the TEXT-affinity ``value`` column (never a lossy float).
        """
        s_str = start.strftime("%Y-%m-%d")
        e_str = end.strftime("%Y-%m-%d")
        rows = self._conn().execute(
            "SELECT date, value FROM rates_multi "
            "WHERE currency = ? AND rate_type = ? "
            "AND date BETWEEN ? AND ?",
            (currency, rate_type, s_str, e_str),
        ).fetchall()
        result: dict[date, Decimal] = {}
        for d_str, value in rows:
            if value is None:
                continue
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                logger.debug("Skipped unparseable rates_multi date: %s", d_str)
                continue
            result[d] = Decimal(str(value))
        return result

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

        # Run the final TRUNCATE checkpoint on a FRESH connection opened in the
        # closing thread. Doing it on the per-thread connections below fails for
        # any connection created in a worker thread — sqlite3 raises
        # ProgrammingError ("created in a thread can only be used in that same
        # thread"), which is a sqlite3.Error subclass that contextlib.suppress
        # silently ate, so the checkpoint never ran and -wal/-shm accumulated.
        # A brand-new connection owns itself, so the checkpoint always runs.
        with contextlib.suppress(sqlite3.Error):
            checkpoint_conn = sqlite3.connect(self.db_path)
            try:
                checkpoint_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                checkpoint_conn.close()

        for conn in conns:
            try:
                conn.close()
            except sqlite3.ProgrammingError as exc:
                # Cross-thread close attempt: log rather than silently pass so
                # the leak is visible. The TRUNCATE above already flushed the
                # WAL; the OS reclaims the leaked handle at process exit.
                logger.debug(
                    "Could not close cache connection from this thread "
                    "(opened in another thread): %s", exc,
                )
            except sqlite3.Error as exc:
                logger.debug("Error closing cache connection: %s", exc)

        # Drop this thread's cached handle so a later call re-opens cleanly.
        if getattr(self._local, "conn", None) is not None:
            self._local.conn = None

    def __enter__(self) -> "CacheDB":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


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

    This is the canonical accessor (F36): ``core.engine._get_cache``
    delegates here, so the engine, GUI panels, and every other caller share
    ONE instance per process (one WAL connection pool). Import it from
    ``core.database`` rather than reaching into ``core.engine``. Lifecycle
    is owned by the instance itself (an ``atexit`` close is registered by
    ``CacheDB.__init__``).
    """
    global _cache_singleton
    if _cache_singleton is None:
        with _cache_singleton_lock:
            if _cache_singleton is None:  # double-check after lock
                _cache_singleton = CacheDB()
    return _cache_singleton
