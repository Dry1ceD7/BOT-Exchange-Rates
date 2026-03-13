#!/usr/bin/env python3
import sys
import os
import csv
import ssl
import json
import argparse
import asyncio
import sqlite3
import subprocess
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

# Parse fixed holidays from config (convert MM-DD string to integer tuple key)
FIXED_THAI_HOLIDAYS = {}
for date_str, holiday_name in config["fixed_holidays"].items():
    month, day = map(int, date_str.split("-"))
    FIXED_THAI_HOLIDAYS[(month, day)] = holiday_name

# ─── Async API Client with Retries ───────────────────────────
async def bot_api_get_async(session: aiohttp.ClientSession, full_url: str, auth_token: str, retries: int = 3) -> Optional[Dict[str, Any]]:
    """Fetches data from BOT API asychronously with exponential backoff retries."""
    headers = {"Authorization": auth_token, "accept": "application/json"}
    
    for attempt in range(1, retries + 1):
        try:
            async with session.get(full_url, headers=headers, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as response:
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
    parser = argparse.ArgumentParser(description="Bank of Thailand Exchange Rate Data Generator")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start date in YYYY-MM-DD format")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date in YYYY-MM-DD format")
    parser.add_argument("--currencies", nargs="+", default=config["currencies"], help="List of currency codes to fetch (e.g. USD EUR JPY)")
    parser.add_argument("--format", type=str, choices=["csv", "json", "sqlite"], default="csv", help="Output export format")
    parser.add_argument("--install-cron", action="store_true", help="Install a background schedule on your Mac")
    args = parser.parse_args()
    
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        sys.exit("Error: Dates must be in YYYY-MM-DD format.")
        
    if start_date > end_date:
        sys.exit("Error: Start date cannot be after end date.")
        
    return start_date, end_date, args.currencies, args.format, args.install_cron

def write_csv(rows: List[Dict[str, Any]], output_path: str, currencies: List[str]):
    columns = ["Year", "Date"]
    for ccy in currencies:
        columns.extend([f"{ccy}_Buying_TT", f"{ccy}_Selling"])
    columns.append("Remark")
    
    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

def write_json(rows: List[Dict[str, Any]], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=4, ensure_ascii=False)

def write_sqlite(rows: List[Dict[str, Any]], output_path: str, currencies: List[str]):
    conn = sqlite3.connect(output_path)
    cursor = conn.cursor()
    
    cols = ["Year INTEGER", "Date TEXT", "Remark TEXT"]
    for ccy in currencies:
        cols.extend([f"{ccy}_Buying_TT TEXT", f"{ccy}_Selling TEXT"])
        
    cursor.execute("DROP TABLE IF EXISTS exchange_rates")
    cursor.execute(f"CREATE TABLE exchange_rates (id INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(cols)})")
    
    # Insert new data
    for row in rows:
        placeholders = ", ".join(["?"] * (len(row)))
        keys = list(row.keys())
        values = [row[k] for k in keys]
        cursor.execute(f"INSERT INTO exchange_rates ({', '.join(keys)}) VALUES ({placeholders})", values)
        
    conn.commit()
    conn.close()

def install_cron_job():
    script_path = os.path.abspath(__file__)
    cron_command = f"0 18 * * 1-5 {sys.executable} {script_path} --format sqlite"
    
    try:
        # Get current crontab
        current_cron = subprocess.check_output("crontab -l", shell=True, text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        current_cron = ""
        
    if script_path in current_cron:
        print("Cron job is already installed.")
        return
        
    new_cron = current_cron.strip() + f"\n{cron_command}\n"
    
    # Install new crontab
    process = subprocess.Popen("crontab -", stdin=subprocess.PIPE, shell=True)
    process.communicate(new_cron.encode())
    
    print("\n✅ Scheduled Cron Job Installed.")
    print(f"   Command: {cron_command}")
    print("   This script will now run automatically every weekday at 6:00 PM.")
    print("   To remove it, run `crontab -e` in your terminal.\n")

# ─── Async Main Execution ────────────────────────────────────
async def main():
    start_date, end_date, currencies, export_format, install_cron = parse_args()
    
    if install_cron:
        install_cron_job()
        
    print(f"Starting BOT Generator (Async) from {start_date} to {end_date}...")
    print(f"Currencies: {', '.join(currencies)}  |  Format: {export_format.upper()}")
    
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
            
            for currency_code in currencies:
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
        row = {
            "Year": current_date.year,
            "Date": date_string,
        }
        
        for ccy in currencies:
            row[f"{ccy}_Buying_TT"] = day_rates.get(ccy, {}).get("buying_tt", "")
            row[f"{ccy}_Selling"]   = day_rates.get(ccy, {}).get("selling", "")
            
        row["Remark"] = remark
        all_rows.append(row)
        current_date += timedelta(days=1)

    # 7. Write to File
    base_name = os.path.splitext(OUTPUT_FILE)[0]
    out_path = f"{base_name}.csv"  # default fallback
    if export_format == "csv":
        out_path = f"{base_name}.csv"
        write_csv(all_rows, out_path, currencies)
    elif export_format == "json":
        out_path = f"{base_name}.json"
        write_json(all_rows, out_path)
    elif export_format == "sqlite":
        out_path = f"{base_name}.db"
        write_sqlite(all_rows, out_path, currencies)
        
    print("=" * 60)
    print("  DONE!")
    print(f"  Rows written: {len(all_rows)}")
    print(f"  Trading days: {len(exchange_rates)}")
    print(f"  Output saved: {os.path.basename(out_path)}")
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
#
# 2026-03-13 | v1.1.0 — Reliability Update
#            | - Standardized aiohttp.ClientTimeout across all API calls
#            | - Fixed out_path unbound variable bug in main script
#            | - Fixed SQLite data duplication (implemented DROP TABLE on re-run)
#            | - Removed duplicate import subprocess
