#!/usr/bin/env python3
import sys
import os
import csv
import ssl
import json
import argparse
import asyncio
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
    import subprocess
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

# ─── Configuration ───────────────────────────────────────────
GATEWAY_URL = "https://gateway.api.bot.or.th"
EXCHANGE_RATE_PATH = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOLIDAY_PATH = "/financial-institutions-holidays/"

MAX_DAYS_PER_REQUEST = 30

DATA_OUTPUT_DIR = os.path.join(PARENT_DIR, "data", "output")
if os.path.exists(DATA_OUTPUT_DIR):
    OUTPUT_FILE = os.path.join(DATA_OUTPUT_DIR, "BOT_Exchange_rates.csv")
else:
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, "BOT_Exchange_rates.csv")

ssl_context = ssl.create_default_context()

FIXED_THAI_HOLIDAYS = {
    (1, 1):   "New Year's Day",
    (4, 6):   "Chakri Memorial Day",
    (4, 13):  "Songkran Festival",
    (4, 14):  "Songkran Festival",
    (4, 15):  "Songkran Festival",
    (5, 1):   "National Labour Day",
    (6, 3):   "H.M. Queen Suthida's Birthday",
    (7, 28):  "H.M. King Vajiralongkorn's Birthday",
    (8, 12):  "H.M. Queen Sirikit's Birthday / Mother's Day",
    (10, 13): "King Bhumibol Memorial Day",
    (10, 23): "Chulalongkorn Memorial Day",
    (12, 5):  "King Bhumibol's Birthday / Father's Day",
    (12, 10): "Constitution Day",
    (12, 31): "New Year's Eve",
}

# ─── Async API Client with Retries ───────────────────────────
async def bot_api_get_async(session: aiohttp.ClientSession, full_url: str, auth_token: str, retries: int = 3) -> Optional[Dict[str, Any]]:
    """Fetches data from BOT API asychronously with exponential backoff retries."""
    headers = {"Authorization": auth_token, "accept": "application/json"}
    
    for attempt in range(1, retries + 1):
        try:
            async with session.get(full_url, headers=headers, ssl=ssl_context, timeout=30) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"  [Warn] API returned {response.status} for {full_url}. Retrying...")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"  [Warn] Connection error ({type(e).__name__}) for {full_url}. Retrying ({attempt}/{retries})...")
            
        if attempt < retries:
            await asyncio.sleep(2 ** attempt) # Exponential backoff: 2s, 4s, 8s...
            
    print(f"  [Error] Failed to fetch {full_url} after {retries} attempts.")
    return None

def parse_args():
    parser = argparse.ArgumentParser(description="Bank of Thailand Exchange Rate CSV Generator")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start date in YYYY-MM-DD format")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date in YYYY-MM-DD format")
    args = parser.parse_args()
    
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        sys.exit("Error: Dates must be in YYYY-MM-DD format.")
        
    if start_date > end_date:
        sys.exit("Error: Start date cannot be after end date.")
        
    return start_date, end_date

def write_csv(rows: List[Dict[str, Any]], output_path: str):
    columns = ["Year", "Date", "USD_Buying_TT", "USD_Selling",
               "EUR_Buying_TT", "EUR_Selling", "Remark"]
    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

# ─── Async Main Execution ────────────────────────────────────
async def main():
    start_date, end_date = parse_args()
    print(f"Starting BOT Generator (Async) from {start_date} to {end_date}...")
    
    holidays: Dict[str, str] = {}
    exchange_rates: Dict[str, Dict[str, Dict[str, str]]] = {}
    
    async with aiohttp.ClientSession() as session:
        # 1. Prepare Holiday Tasks
        start_year = start_date.year
        end_year = end_date.year
        holiday_tasks = []
        for year in range(start_year, end_year + 1):
            url = f"{GATEWAY_URL}{HOLIDAY_PATH}?year={year}"
            holiday_tasks.append(bot_api_get_async(session, url, TOKEN_HOLIDAY))
            
        # 2. Prepare Exchange Rate Tasks
        rate_tasks = []
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=MAX_DAYS_PER_REQUEST), end_date)
            start_str = chunk_start.strftime("%Y-%m-%d")
            end_str = chunk_end.strftime("%Y-%m-%d")
            
            for currency_code in ("USD", "EUR"):
                url = (f"{GATEWAY_URL}{EXCHANGE_RATE_PATH}?start_period={start_str}"
                       f"&end_period={end_str}&currency={currency_code}")
                # We attach the currency code so we know how to parse the result later
                rate_tasks.append((currency_code, bot_api_get_async(session, url, TOKEN_EXCHANGE_RATE)))
                
            chunk_start = chunk_end + timedelta(days=1)
            
        print(f"  Fetching data ({len(holiday_tasks)} holiday years, {len(rate_tasks)} rate chunks)...")
        
        # 3. Execute all tasks concurrently!
        holiday_results = await asyncio.gather(*holiday_tasks)
        rate_results = await asyncio.gather(*(task[1] for task in rate_tasks))
        
        # 4. Process Holiday Results
        for response_data in holiday_results:
            if response_data:
                holiday_list = response_data.get("result", {}).get("data", [])
                if isinstance(holiday_list, list):
                    for holiday_entry in holiday_list:
                        h_date = str(holiday_entry.get("Date", "")).strip()[:10]
                        h_name = str(holiday_entry.get("HolidayDescription", "Holiday")).strip()
                        if h_date:
                            holidays[h_date] = h_name
                            
        # 5. Process Rate Results
        for (currency_code, _), response_data in zip(rate_tasks, rate_results):
            if not response_data:
                continue
                
            try:
                data_detail_list = response_data.get("result", {}).get("data", {}).get("data_detail", [])
            except (KeyError, AttributeError):
                continue
                
            if not isinstance(data_detail_list, list):
                continue
                
            for rate_entry in data_detail_list:
                r_date = str(rate_entry.get("period", "")).strip()[:10]
                if not r_date:
                    continue
                    
                buying_tt = str(rate_entry.get("buying_transfer", "")).strip()
                selling_rate = str(rate_entry.get("selling", "")).strip()
                
                if r_date not in exchange_rates:
                    exchange_rates[r_date] = {}
                    
                exchange_rates[r_date][currency_code] = {
                    "buying_tt": buying_tt,
                    "selling": selling_rate
                }

    # 6. Build the rows
    all_rows = []
    current_date = start_date
    while current_date <= end_date:
        date_string = current_date.strftime("%d %b %Y")
        iso_date = current_date.strftime("%Y-%m-%d")

        holiday_name = holidays.get(iso_date, "")
        if not holiday_name:
            holiday_name = FIXED_THAI_HOLIDAYS.get((current_date.month, current_date.day), "")

        is_weekend = (current_date.weekday() >= 5)
        
        remark = ""
        if is_weekend and holiday_name:
            remark = f"Weekend; {holiday_name}"
        elif is_weekend:
            remark = "Weekend"
        elif holiday_name:
            remark = holiday_name
        
        remark = remark.replace(",", ";")

        day_rates = exchange_rates.get(iso_date, {})
        all_rows.append({
            "Year":          current_date.year,
            "Date":          date_string,
            "USD_Buying_TT": day_rates.get("USD", {}).get("buying_tt", ""),
            "USD_Selling":   day_rates.get("USD", {}).get("selling", ""),
            "EUR_Buying_TT": day_rates.get("EUR", {}).get("buying_tt", ""),
            "EUR_Selling":   day_rates.get("EUR", {}).get("selling", ""),
            "Remark":        remark,
        })
        current_date += timedelta(days=1)

    # 7. Write to CSV
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
# 2026-03-11 | v1.03 — Overhaul
#            | - Standardized date format to "DD MMM YYYY" (e.g. 04 Feb 2026)
#            | - Fixed output filename to BOT_Exchange_rates.csv (removed date suffix)
#            | - Added detailed progress logging in the terminal
#            | - Added iso_date for internal logic while keeping formatted display
# 2026-03-11 | v1.0.7 — Optimizations
#            | - Implemented argparse for --start and --end CLI arguments
#            | - Switched to asyncio and aiohttp for massive download speedup
#            | - Implemented exponential backoff and retries for network resilience
#            | - Resolved Pyre warnings by adding strict types and .get() safety checks
