#!/usr/bin/env python3
import gc
import sys
import os
import csv
import ssl
import json
import argparse
import asyncio
import sqlite3
import subprocess
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, List
from datetime import date, timedelta, datetime

# ─── Ensure local libraries are installed (aiohttp) ───────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = SCRIPT_DIR
_LIBS_DIR = os.path.join(PARENT_DIR, "_libs")

if not os.path.exists(_LIBS_DIR):
    os.makedirs(_LIBS_DIR)

if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

try:
    import aiohttp
except ImportError:
    print("Installing required package 'aiohttp' locally...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--target", _LIBS_DIR, "aiohttp"])
    import aiohttp

# ─── Read tokens from .env ────────────────────────────────────
env_path_local = os.path.join(SCRIPT_DIR, ".env")
env_path_parent = os.path.join(PARENT_DIR, ".env")
env_path = env_path_parent if os.path.exists(env_path_parent) else env_path_local

if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key, val = parts
                    os.environ[key.strip()] = val.strip().strip("\"'")

TOKEN_EXCHANGE_RATE = os.environ.get("BOT_TOKEN_EXG", "")
TOKEN_HOLIDAY = os.environ.get("BOT_TOKEN_HOL", "")

if not TOKEN_EXCHANGE_RATE or not TOKEN_HOLIDAY:
    sys.exit("Error: Missing BOT API tokens in .env file.")

# ─── Load Centralized Config ───────────────────────────────────
CONFIG_FILE = os.path.join(PARENT_DIR, "config.json")
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
    
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

GATEWAY_URL = config["api"]["gateway_url"]
EXCHANGE_RATE_PATH = config["api"]["exchange_rate_path"]
HOLIDAY_PATH = config["api"]["holiday_path"]
MAX_DAYS_PER_REQUEST = config["api"]["max_days_per_request"]

DATA_OUTPUT_DIR = os.path.join(PARENT_DIR, "data", "output")
if os.path.exists(DATA_OUTPUT_DIR):
    OUTPUT_FILE = os.path.join(DATA_OUTPUT_DIR, "BOT_Exchange_rates.csv")
else:
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, "BOT_Exchange_rates.csv")

ssl_context = ssl.create_default_context()

# Parse fixed holidays from config
FIXED_THAI_HOLIDAYS = {}
for date_str, holiday_name in config["fixed_holidays"].items():
    month, day = map(int, date_str.split("-"))
    FIXED_THAI_HOLIDAYS[(month, day)] = holiday_name

def count_business_days(start: date, end: date, holidays: Dict[str, str]) -> int:
    """Calculates number of trading days in range (excluding weekends and BOT holidays)."""
    days = 0
    curr = start
    while curr <= end:
        ds = curr.strftime("%Y-%m-%d")
        is_wknd = curr.weekday() >= 5
        is_hol = ds in holidays or (curr.month, curr.day) in FIXED_THAI_HOLIDAYS
        if not is_wknd and not is_hol:
            days += 1
        curr += timedelta(days=1)
    return days

# ─── Async API Client with Retries ───────────────────────────
async def bot_api_get_async(session: aiohttp.ClientSession, full_url: str, auth_token: str, retries: int = 3) -> Optional[Dict[str, Any]]:
    headers = {"Authorization": auth_token, "accept": "application/json"}
    for attempt in range(1, retries + 1):
        try:
            async with session.get(full_url, headers=headers, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    # Specific handling for throttling
                    await asyncio.sleep(attempt * 2)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        if attempt < retries:
            await asyncio.sleep(2 ** attempt)
    return None

def parse_args():
    parser = argparse.ArgumentParser(description="Bank of Thailand Exchange Rate Data Generator")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date YYYY-MM-DD")
    parser.add_argument("--currencies", nargs="+", default=config["currencies"], help="List of currency codes")
    parser.add_argument("--format", type=str, choices=["csv", "json", "sqlite"], default="csv", help="Output format")
    args = parser.parse_args()
    return datetime.strptime(args.start, "%Y-%m-%d").date(), datetime.strptime(args.end, "%Y-%m-%d").date(), args.currencies, args.format

def write_csv(rows: List[Dict[str, Any]], output_path: str, currencies: List[str]):
    columns = ["Year", "Date"]
    for ccy in currencies: columns.extend([f"{ccy}_Buying_TT", f"{ccy}_Selling"])
    columns.append("Remark")
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

# ─── Async Main Execution ────────────────────────────────────
async def main():
    start_date, end_date, currencies, export_format = parse_args()
    db_path = os.path.join(SCRIPT_DIR, "bot_rates_cache.db")
    
    # 0. Init Cache
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS exchange_rates (date TEXT, currency TEXT, buying_tt REAL, selling REAL, PRIMARY KEY(date,currency))")

    print(f"Starting BOT Generator (Async+Cache) from {start_date} to {end_date}...")
    
    holidays: Dict[str, str] = {}
    exchange_rates: Dict[str, Dict[str, Dict[str, str]]] = {}
    
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(1) 
        
        async def fetch_bounded(url, token, is_rate=False, ccy=None):
            async with sem:
                if is_rate:
                    import urllib.parse
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    s, e = qs.get("start_period", [""])[0], qs.get("end_period", [""])[0]
                    with sqlite3.connect(db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT date, buying_tt, selling FROM exchange_rates WHERE date BETWEEN ? AND ? AND currency = ?", [s, e, ccy])
                        cached = cursor.fetchall()
                        
                        # Dynamic validation: check if cached count matches expected trading days
                        s_dt = datetime.strptime(s, "%Y-%m-%d").date()
                        e_dt = datetime.strptime(e, "%Y-%m-%d").date()
                        expected = count_business_days(s_dt, e_dt, holidays)
                        
                        if len(cached) >= expected and expected > 0:
                            details = [{"period": d, "buying_transfer": b, "selling": s} for d, b, s in cached]
                            return {"result": {"data": {"data_detail": details}}}
                
                res = await bot_api_get_async(session, url, token)
                if is_rate and res:
                    details = res.get("result", {}).get("data", {}).get("data_detail", [])
                    if details:
                        with sqlite3.connect(db_path) as conn:
                            conn.executemany("INSERT OR REPLACE INTO exchange_rates VALUES (?, ?, ?, ?)",
                                           [(d["period"], ccy, d["buying_transfer"], d["selling"]) for d in details])
                return res

        # 1. Holiday Tasks (Fetch FIRST for cache validation)
        holiday_tasks = []
        for year in range(start_date.year, end_date.year + 1):
            url = f"{GATEWAY_URL}{HOLIDAY_PATH}?year={year}"
            holiday_tasks.append(fetch_bounded(url, TOKEN_HOLIDAY))
        
        print(f"  Fetching holidays...")
        h_res = await asyncio.gather(*holiday_tasks)
        for r in h_res:
            if r:
                for h in r.get("result", {}).get("data", []):
                    holidays[h["Date"]] = h["HolidayDescription"]
            
        # 2. Rate Tasks (Now with accurate holiday-aware expected counts)
        rate_tasks = []
        cs = start_date
        while cs <= end_date:
            ce = min(cs + timedelta(days=MAX_DAYS_PER_REQUEST), end_date)
            for ccy in currencies:
                url = f"{GATEWAY_URL}{EXCHANGE_RATE_PATH}?start_period={cs}&end_period={ce}&currency={ccy}"
                rate_tasks.append((ccy, fetch_bounded(url, TOKEN_EXCHANGE_RATE, True, ccy)))
            cs = ce + timedelta(days=1)
            
        print(f"  Processing {len(rate_tasks)} rate chunks...")

        for ccy, task in rate_tasks:
            res = await task
            if res:
                details = res.get("result", {}).get("data", {}).get("data_detail", [])
                for d in details:
                    dt = d["period"]
                    if dt not in exchange_rates: exchange_rates[dt] = {}
                    exchange_rates[dt][ccy] = {"buying_tt": str(d["buying_transfer"]), "selling": str(d["selling"])}

    # 3. Build Rows
    all_rows = []
    curr = start_date
    while curr <= end_date:
        ds = curr.strftime("%Y-%m-%d")
        h_name = holidays.get(ds, "") or FIXED_THAI_HOLIDAYS.get((curr.month, curr.day), "")
        is_wknd = curr.weekday() >= 5
        remark = f"{h_name}; Weekend" if is_wknd and h_name else ("Weekend" if is_wknd else h_name)
        
        row = {"Year": curr.year, "Date": curr.strftime("%d_%m_%Y"), "Remark": remark.replace(",", ";")}
        for ccy in currencies:
            d_rates = exchange_rates.get(ds, {}).get(ccy, {})
            row[f"{ccy}_Buying_TT"] = d_rates.get("buying_tt", "")
            row[f"{ccy}_Selling"] = d_rates.get("selling", "")
        all_rows.append(row)
        curr += timedelta(days=1)

    write_csv(all_rows, OUTPUT_FILE, currencies)
    print(f"  DONE! Saved to: {OUTPUT_FILE}")

    # 4. Integrate bot_acc_filler.py (v1.3.9 Enablement)
    filler_script = os.path.join(SCRIPT_DIR, "bot_acc_filler.py")
    acc_input = config.get("accounting", {}).get("input_file", "")
    if acc_input and os.path.exists(os.path.join(SCRIPT_DIR, acc_input)):
        print(f"\nEnabling Finance Accounting Filler for '{acc_input}'...")
        try:
            subprocess.check_call([sys.executable, filler_script])
        except Exception as e:
            print(f"  [Warn] Accounting Filler failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())

# ─── Changelog ───────────────────────────────────────────────
# Every update to this file should add a new entry below.
# Format:  Date | Who | What changed
#
# 2026-03-09 | Initial version (GitHub)
#            | - Fetches USD and EUR rates from BOT API in 30-day chunks
#            | - Fetches public holidays from BOT API
#            | - Outputs CSV by printing each row to stdout (required piping to a file)
#
# 2026-03-09 | Update
#            | ADDED:   import csv
#            | ADDED:   OUTPUT_FILE variable — auto-named with today's date
#            | ADDED:   all_rows list — collects rows before writing
#            | ADDED:   write_csv() function — writes the CSV file directly
#            |          (no more manual piping: python3 bot_generator.py > file.csv)
#            | ADDED:   comments/explanation on every section
#            | REMOVED: import urllib.error (was unused in original)
#            | REMOVED: year_string variable (replaced by current_date.year inline)
#            | REMOVED: 4 separate rate variables before print() (merged into dict)
#            | REMOVED: print() header + print() row loop (replaced by write_csv)
#            | NOT CHANGED: all logic, all config constants, all variable names
#
# 2026-03-11 | v1.03 — Overhaul
#            | - Standardized date format to "DD MMM YYYY" (e.g. 04 Feb 2026)
#            | - Fixed output filename to BOT_Exchange_rates.csv (removed date suffix)
#            | - Added detailed progress logging in the terminal
#            | - Added iso_date for internal logic while keeping formatted display
#
# 2026-03-11 | v1.0.7 — Optimizations
#            | - Implemented argparse for --start and --end CLI arguments
#            | - Switched to asyncio and aiohttp for massive download speedup
#            | - Implemented exponential backoff and retries for network resilience
#            | - Resolved Pyre warnings by adding strict types and .get() safety checks
#
# 2026-03-13 | v1.1.0 — Reliability Update
#            | - Standardized aiohttp.ClientTimeout across all API calls
#            | - Fixed out_path unbound variable bug in main script
#            | - Fixed SQLite data duplication (implemented DROP TABLE on re-run)
#            | - Removed duplicate import subprocess
#
# 2026-03-13 | v1.1.2 — Infrastructure Alignment
#            | - Fully enabled config.json integration (removed hardcoded fallbacks)
#            | - Synchronized fixed holidays and default currencies with central config.json
#
# 2026-03-13 | v1.2.0 — Quality of Life Upgrade
#            | - Financial precision: exchange rates parsed via decimal.Decimal
#            | - Performance: TCPConnector(limit=10, keepalive_timeout=30) for connection pooling
#            | - Reliability: explicit ClientTimeout(connect=15, total=45)
#            | - Memory: gc.collect() after heavy data-fetch phase
#
# 2026-03-15 | v1.3.9 — Date Format Update
#            | - Updated date format to dd_mm_yyyy in both generator and Excel report.
# # v1.3.9 | 2026-03-16 | Robust Cache Validation (Business Day Count)
#            | - Balanced cache trigger (len >= expected_days instead of fixed 28)
#            | - Enabled automatic bot_acc_filler integration
#            | - Improved PC-portability with standard lib handling
#            | - Hardened literal v1.3.0 scripts for production use with SQLite persistence.
