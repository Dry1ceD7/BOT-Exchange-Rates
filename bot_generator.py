#!/usr/bin/env python3
"""
================================================================================
  BOT Exchange Rate CSV Generator
  ────────────────────────────────
  Fetches USD and EUR exchange rates from the Bank of Thailand (BOT) API
  and writes them to a CSV file with holiday annotations.

  Usage:
    python3 bot_generator.py                                # default period
    python3 bot_generator.py --start 2024-01-01             # custom start
    python3 bot_generator.py --start 2024-01-01 --end 2024-12-31
================================================================================
"""

# ─── Standard library imports ────────────────────────────────
import sys
import os
import csv
import argparse
import asyncio
from typing import Dict, Any, List
from datetime import date, timedelta, datetime

# ─── Ensure the local _libs folder is on path ────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(SCRIPT_DIR, "_libs")
if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

# ─── Import the shared core module ───────────────────────────
# bot_core.py centralizes: .env loading, aiohttp install, async API,
# holiday/rate fetching, date parsing, and constant definitions.
from bot_core import (  # noqa: E402
    fetch_all_data,
    FIXED_HOLIDAYS,
)

# ─── Configuration ───────────────────────────────────────────
DATA_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data", "output")
if os.path.exists(DATA_OUTPUT_DIR):
    OUTPUT_FILE = os.path.join(DATA_OUTPUT_DIR, "BOT_Exchange_rates.csv")
else:
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, "BOT_Exchange_rates.csv")


# ═══════════════════════════════════════════════════════════════
# CLI ARGUMENT PARSER
# ═══════════════════════════════════════════════════════════════

def parse_args():
    """Parse command-line arguments for the CSV generator."""
    parser = argparse.ArgumentParser(
        description="Bank of Thailand Exchange Rate CSV Generator"
    )
    parser.add_argument(
        "--start", type=str, default="2025-01-01",
        help="Start date in YYYY-MM-DD format (default: 2025-01-01)",
    )
    parser.add_argument(
        "--end", type=str, default=datetime.now().strftime("%Y-%m-%d"),
        help="End date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        sys.exit("Error: Dates must be in YYYY-MM-DD format.")

    if start_date > end_date:
        sys.exit("Error: Start date cannot be after end date.")

    return start_date, end_date


# ═══════════════════════════════════════════════════════════════
# CSV WRITER
# ═══════════════════════════════════════════════════════════════

def write_csv(rows: List[Dict[str, Any]], output_path: str):
    """Write all rows to a CSV file with the standard column layout."""
    columns = [
        "Year", "Date", "USD_Buying_TT", "USD_Selling",
        "EUR_Buying_TT", "EUR_Selling", "Remark",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ═══════════════════════════════════════════════════════════════
# MAIN ASYNC PIPELINE
# ═══════════════════════════════════════════════════════════════

async def main():
    """Fetch data via bot_core and generate the CSV output."""
    start_date, end_date = parse_args()

    print("=" * 60)
    print("  BOT Exchange Rate CSV Generator")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Period: {start_date} → {end_date}")
    print("=" * 60)

    # ── Fetch all data concurrently via bot_core ──────────────
    holidays, exchange_rates = await fetch_all_data(start_date, end_date)

    # ── Build row data ────────────────────────────────────────
    all_rows: List[Dict[str, Any]] = []
    current_date = start_date

    while current_date <= end_date:
        date_string = current_date.strftime("%d %b %Y")
        iso_date = current_date.strftime("%Y-%m-%d")

        # Determine the remark (holiday name, weekend, or blank)
        holiday_name = holidays.get(iso_date, "")
        if not holiday_name:
            holiday_name = FIXED_HOLIDAYS.get(
                (current_date.month, current_date.day), ""
            )

        is_weekend = current_date.weekday() >= 5

        if is_weekend and holiday_name:
            remark = f"Weekend; {holiday_name}"
        elif is_weekend:
            remark = "Weekend"
        elif holiday_name:
            remark = holiday_name
        else:
            remark = ""

        remark = remark.replace(",", ";")

        # Look up rates for this day
        day_rates = exchange_rates.get(iso_date, {})
        usd = day_rates.get("USD", {})
        eur = day_rates.get("EUR", {})

        all_rows.append({
            "Year":          current_date.year,
            "Date":          date_string,
            "USD_Buying_TT": usd.get("buying", "") if usd else "",
            "USD_Selling":   usd.get("selling", "") if usd else "",
            "EUR_Buying_TT": eur.get("buying", "") if eur else "",
            "EUR_Selling":   eur.get("selling", "") if eur else "",
            "Remark":        remark,
        })
        current_date += timedelta(days=1)

    # ── Write CSV ─────────────────────────────────────────────
    write_csv(all_rows, OUTPUT_FILE)

    print("=" * 60)
    print("  DONE!")
    print(f"  Rows written: {len(all_rows)}")
    print(f"  Trading days: {len(exchange_rates)}")
    print(f"  Output saved: {os.path.basename(OUTPUT_FILE)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())


# ─── Changelog ───────────────────────────────────────────────
# Every update to this file should add a new entry below.
#
# 2026-03-09 | v1 — Initial version
#            | - Fetches USD/EUR rates from BOT API in 30-day chunks
#            | - Fetches public holidays from BOT API
#            | - Outputs CSV by printing each row to stdout
#
# 2026-03-09 | v1.01 — CSV file writer
#            | - Added write_csv() function
#            | - Auto-named output file
#
# 2026-03-11 | v1.03 — Overhaul
#            | - Standardized date format to "DD MMM YYYY"
#            | - Fixed output filename to BOT_Exchange_rates.csv
#
# 2026-03-11 | v1.0.7 — Optimizations
#            | - Implemented argparse for --start and --end CLI arguments
#            | - Switched to asyncio and aiohttp for massive download speedup
#            | - Implemented exponential backoff and retries for network resilience
#
# 2026-03-12 | v2.0 — Core Module Extraction
#            | - Replaced all duplicated .env/API/holiday logic with bot_core.py
#            | - Script is now ~130 lines instead of ~287
#            | - Rate dict keys changed: "buying_tt" → "buying" (unified with core)
