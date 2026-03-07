#!/usr/bin/env python3
import sys
import json
import ssl
import os
import urllib.request
import urllib.error
from datetime import date, timedelta, datetime

# ─── Load Environment Variables ─────────────────────────────
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("\"'")

TOKEN_EXCHANGE_RATE = os.environ.get("BOT_TOKEN_EXG", "")
TOKEN_HOLIDAY = os.environ.get("BOT_TOKEN_HOL", "")

if not TOKEN_EXCHANGE_RATE or not TOKEN_HOLIDAY:
    sys.exit("Error: Missing BOT API tokens in .env file.")

# ─── Configuration ───────────────────────────────────────────
GATEWAY_URL = "https://gateway.api.bot.or.th"
EXCHANGE_RATE_PATH = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOLIDAY_PATH = "/financial-institutions-holidays/"
START_DATE = date(2025, 1, 1)
END_DATE = date.today()
MAX_DAYS_PER_REQUEST = 30
ssl_context = ssl.create_default_context()

# ─── Fixed Thai Calendar Holidays ────────────────────────────
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
    request = urllib.request.Request(
        full_url,
        headers={"Authorization": auth_token, "accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, context=ssl_context, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

# ─── Fetch Holidays ──────────────────────────────────────────
holidays = {}
start_year = START_DATE.year
end_year = END_DATE.year

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

# ─── Fetch Exchange Rates ────────────────────────────────────
exchange_rates = {}
chunk_start_date = START_DATE

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

# ─── Generate CSV Output ─────────────────────────────────────
print("Year,Date,USD_Buying_TT,USD_Selling,EUR_Buying_TT,EUR_Selling,Remark")

current_date = START_DATE
while current_date <= END_DATE:
    date_string = current_date.strftime("%Y-%m-%d")
    year_string = current_date.strftime("%Y")

    holiday_name = holidays.get(date_string, "")
    if not holiday_name:
        holiday_name = FIXED_THAI_HOLIDAYS.get((current_date.month, current_date.day), "")

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

    day_rates = exchange_rates.get(date_string, {})
    usd_buying_tt = day_rates.get("USD", {}).get("buying_tt", "")
    usd_selling = day_rates.get("USD", {}).get("selling", "")
    eur_buying_tt = day_rates.get("EUR", {}).get("buying_tt", "")
    eur_selling = day_rates.get("EUR", {}).get("selling", "")

    print(f"{year_string},{date_string},{usd_buying_tt},{usd_selling},{eur_buying_tt},{eur_selling},{remark}")
    current_date += timedelta(days=1)
