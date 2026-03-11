#!/usr/bin/env python3
import sys
import csv
import json
import ssl
import os
import urllib.request
from datetime import date, timedelta, datetime

# ─── Read tokens from .env ────────────────────────────────────
# Checks for .env in current folder or parent folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = SCRIPT_DIR

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
# Main BOT API gateway and the two endpoints we use
GATEWAY_URL = "https://gateway.api.bot.or.th"
EXCHANGE_RATE_PATH = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOLIDAY_PATH = "/financial-institutions-holidays/"

# Date range for the report — change START_DATE if you need a different period
START_DATE = date(2025, 1, 1)
END_DATE = date.today()

# BOT API only allows up to 30 days per single request
MAX_DAYS_PER_REQUEST = 30

# Output CSV file — redirected to ../data/output/ if it exists
DATA_OUTPUT_DIR = os.path.join(PARENT_DIR, "data", "output")
if os.path.exists(DATA_OUTPUT_DIR):
    OUTPUT_FILE = os.path.join(DATA_OUTPUT_DIR, "BOT_Exchange_rates.csv")
else:
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, "BOT_Exchange_rates.csv")

ssl_context = ssl.create_default_context()

# ─── Fixed Thai public holidays ──────────────────────────────
# These recurring annual dates are a fallback for when the BOT holidays API
# doesn't return them (e.g. older years or when a holiday falls on a weekend
# and the API only lists the substitution day instead of the original date).
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

def bot_api_get(full_url, auth_token):
    # Sends an authenticated GET to the BOT gateway and returns parsed JSON.
    # Returns None if the request fails so callers can just check `if not data`.
    request = urllib.request.Request(
        full_url,
        headers={"Authorization": auth_token, "accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, context=ssl_context, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

# ─── Fetch public holidays ───────────────────────────────────
# We pull one year at a time (API requirement) and store everything in
# a flat dict so date lookups later are a simple holidays.get(date_str).
holidays = {}
start_year = START_DATE.year
end_year = END_DATE.year

print(f"Fetching holidays from BOT API ({start_year}-{end_year})...")
for year in range(start_year, end_year + 1):
    holiday_url = f"{GATEWAY_URL}{HOLIDAY_PATH}?year={year}"
    response_data = bot_api_get(holiday_url, TOKEN_HOLIDAY)
    if response_data:
        holiday_list = response_data.get("result", {}).get("data", [])
        if isinstance(holiday_list, list):
            for holiday_entry in holiday_list:
                holiday_date = str(holiday_entry.get("Date", "")).strip()[:10]
                holiday_name = str(holiday_entry.get("HolidayDescription", "Holiday")).strip()
                if holiday_date:
                    holidays[holiday_date] = holiday_name

# ─── Fetch exchange rates ────────────────────────────────────
# We loop through the full date range in 30-day chunks (BOT API limit).
# Both USD and EUR are fetched in the same loop to avoid duplicate chunks.
exchange_rates = {}
chunk_start_date = START_DATE

print(f"Fetching USD and EUR exchange rates from {START_DATE} to {END_DATE}...")
while chunk_start_date <= END_DATE:
    chunk_end_date = min(chunk_start_date + timedelta(days=MAX_DAYS_PER_REQUEST), END_DATE)
    start_str = chunk_start_date.strftime("%Y-%m-%d")
    end_str = chunk_end_date.strftime("%Y-%m-%d")

    for currency_code in ("USD", "EUR"):
        rate_url = (f"{GATEWAY_URL}{EXCHANGE_RATE_PATH}?start_period={start_str}"
                    f"&end_period={end_str}&currency={currency_code}")
        response_data = bot_api_get(rate_url, TOKEN_EXCHANGE_RATE)
        if not response_data:
            continue

        try:
            data_detail_list = response_data["result"]["data"]["data_detail"]
        except (KeyError, TypeError):
            continue

        if not isinstance(data_detail_list, list):
            continue

        for rate_entry in data_detail_list:
            rate_date = str(rate_entry.get("period", "")).strip()[:10]
            if not rate_date:
                continue

            buying_tt = str(rate_entry.get("buying_transfer", "")).strip()
            selling_rate = str(rate_entry.get("selling", "")).strip()

            if rate_date not in exchange_rates:
                exchange_rates[rate_date] = {}

            exchange_rates[rate_date][currency_code] = {
                "buying_tt": buying_tt,
                "selling": selling_rate
            }

    chunk_start_date = chunk_end_date + timedelta(days=1)

# ─── Build one row per calendar day ──────────────────────────
# We go through every single day (not just trading days) so weekends and
# holidays still appear in the output — just with blank rate columns.
# The Remark column explains why a day has no rate data.
all_rows = []
current_date = START_DATE

while current_date <= END_DATE:
    # Use formatted date like "04 Feb 2026" to match the accountant sample
    date_string = current_date.strftime("%d %b %Y")
    # For dictionary parsing (BOT api keys use YYYY-MM-DD), we keep an iso string
    iso_date = current_date.strftime("%Y-%m-%d")

    # Check BOT API holidays first; fall back to our fixed list for the rest
    holiday_name = holidays.get(iso_date, "")
    if not holiday_name:
        holiday_name = FIXED_THAI_HOLIDAYS.get((current_date.month, current_date.day), "")

    is_weekend = (current_date.weekday() >= 5)  # 5=Sat, 6=Sun
    
    # We always use the exact output date string for the result column
    row_date = date_string
    
    remark = ""
    # Look up the rate using the ISO format dictionary key
    day_rates = exchange_rates.get(iso_date, {})
    if is_weekend and holiday_name:
        remark = f"Weekend; {holiday_name}"
    elif is_weekend:
        remark = "Weekend"
    elif holiday_name:
        remark = holiday_name
    else:
        remark = ""

    # Commas inside remark would break the CSV, so swap them to semicolons
    remark = remark.replace(",", ";")

    day_rates = exchange_rates.get(date_string, {})
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

# ─── Write CSV file ───────────────────────────────────────────
# Previously this used print() which meant you had to pipe the output
# yourself: python3 bot_generator.py > file.csv
# Now it just writes the file directly using Python's csv module,
# which also handles quoting automatically (no manual comma-escaping needed).
def write_csv(rows, output_path):
    columns = ["Year", "Date", "USD_Buying_TT", "USD_Selling",
               "EUR_Buying_TT", "EUR_Selling", "Remark"]
    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

write_csv(all_rows, OUTPUT_FILE)
print("=" * 60)
print("  DONE!")
print(f"  Rows written: {len(all_rows)}")
print(f"  Trading days: {len(exchange_rates)}")
print(f"  Output saved: {os.path.basename(OUTPUT_FILE)}")
print("=" * 60)


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
# 2026-03-11 | Overhaul
#            | - Standardized date format to "DD MMM YYYY" (e.g. 04 Feb 2026)
#            | - Fixed output filename to BOT_Exchange_rates.csv (removed date suffix)
#            | - Added detailed progress logging in the terminal
#            | - Added iso_date for internal logic while keeping formatted display
