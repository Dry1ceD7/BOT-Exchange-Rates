#!/usr/bin/env python3
"""
================================================================================
  BOT Accountant Excel Filler
  Fills "EX Rate" (Selling rate) and "วันที่ดึง Exchange rate date"
  in ANY accountant spreadsheet that follows the same column layout.

  How to run:
    python3 bot_acc_filler.py                        (uses default file)
    python3 bot_acc_filler.py my_accounting_file.xlsx (specify your own file)

  What it does (for each row in each data sheet):
    1. Reads column "Cur" to know if the row is USD or EUR
    2. Reads column "วันที่ใบขน" to get the export entry date
    3. If that date is a weekend or BOT holiday, rolls back to the
       most recent open trading day
    4. Writes the resolved date into "วันที่ดึง Exchange rate date"
    5. Writes the BOT Selling rate into "EX Rate"
    6. Saves as a NEW file (never overwrites the original)

  Notes:
    - Rows with currency "THB" or missing dates are skipped
    - Sheets whose names start with "Exrate" are treated as reference
      data and are not modified
================================================================================
"""

# ─── Standard library imports ────────────────────────────────
import sys
import json
import ssl
import os
import urllib.request
from datetime import date, timedelta, datetime


# ─── Auto-install openpyxl if not already available ──────────
# Installed in _libs (either in same folder or parent folder)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = SCRIPT_DIR

# Possible locations for _libs
_LIBS_LOCAL = os.path.join(SCRIPT_DIR, "_libs")
_LIBS_PARENT = os.path.join(PARENT_DIR, "_libs")

if os.path.exists(_LIBS_PARENT):
    _LIBS = _LIBS_PARENT
else:
    _LIBS = _LIBS_LOCAL

if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)

try:
    import openpyxl
except ImportError:
    print("  Installing openpyxl to local _libs folder...", file=sys.stderr)
    import subprocess
    os.makedirs(_LIBS, exist_ok=True)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "--target", _LIBS, "openpyxl",
        "--break-system-packages"
    ])
    import importlib
    importlib.invalidate_caches()
    import openpyxl


# ─── Load API tokens from the .env file ─────────────────────
# Checks for .env in current folder or parent folder
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

TOKEN_EXG = os.environ.get("BOT_TOKEN_EXG", "")
TOKEN_HOL = os.environ.get("BOT_TOKEN_HOL", "")
if not TOKEN_EXG or not TOKEN_HOL:
    sys.exit("Error: Missing BOT API tokens in .env file.")


# ─── Configuration ───────────────────────────────────────────
# The BOT API gateway URL and the two endpoints we call
GATEWAY   = "https://gateway.api.bot.or.th"
EXG_PATH  = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOL_PATH  = "/financial-institutions-holidays/"

# We fetch rates starting from 2025 to cover any date that might
# appear in the accountant's file. Adjust START if you need older data.
START     = date(2025, 1, 1)
END       = date.today()
CHUNK     = 30   # BOT API only allows 30 days per request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TODAY_STR  = datetime.now().strftime("%Y-%m-%d")

ssl_ctx = ssl.create_default_context()


# ─── Determine which Excel file to process ───────────────────
# If the user passes a file path as a command-line argument, use it.
# Otherwise fall back to the default sample file in the same folder.
if len(sys.argv) > 1:
    # User specified a file path
    INPUT_EXCEL = os.path.abspath(sys.argv[1])
else:
    # Default: look for the sample file
    INPUT_EXCEL = os.path.join(PARENT_DIR, "data", "input", "exchange_rate_file_sample.xlsx")

# Build the output filename: insert "_updated_YYYY-MM-DD" before .xlsx
# Example: "Feb_2026.xlsx" becomes "Feb_2026_updated_2026-03-10.xlsx"
base_name = os.path.splitext(os.path.basename(INPUT_EXCEL))[0]
OUTPUT_EXCEL = os.path.join(
    os.path.dirname(INPUT_EXCEL),
    f"{base_name}_updated.xlsx"
)


# ─── Column header names we look for ────────────────────────
# Instead of hardcoding column numbers (like "Column I = 9"),
# we scan the header row for these keywords. This way the script
# works even if the columns are in a different order or position.
HEADER_CUR       = "Cur"                           # currency code
HEADER_EXPORT_DT = "วันที่ใบขน"                      # export entry date
HEADER_RATE_DT   = "วันที่ดึง Exchange rate date"    # date we write to
HEADER_EX_RATE   = "EX Rate"                        # rate we write to


# ─── Fixed Thai holidays (fallback) ─────────────────────────
# These are used when the BOT API does not return a holiday
# (for example, when it falls on a weekend and only the
# substitution day is listed instead).
FIXED_HOLIDAYS = {
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


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def bot_api_get(full_url, auth_token):
    """Call the BOT API and return the JSON response.
    Returns None on failure so callers can just do: if not data."""
    request = urllib.request.Request(
        full_url,
        headers={"Authorization": auth_token, "accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, context=ssl_ctx, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def parse_date_string(date_str):
    """Turn a date value from Excel into a Python date object.

    The accountant's file might store dates as:
      - A Python datetime (openpyxl reads formatted dates this way)
      - A text string like "04 Feb 2026" or "2026-02-04"
    Returns None if it cannot figure out the format.
    """
    if date_str is None:
        return None

    # openpyxl sometimes gives us a datetime object directly
    if isinstance(date_str, datetime):
        return date_str.date()
    if isinstance(date_str, date):
        return date_str

    # Otherwise try common text formats
    text = str(date_str).strip()
    if not text:
        return None

    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_bot_closed(check_date, holidays_dict):
    """Returns True if the Bank of Thailand is closed on this date.
    Closed means: Saturday, Sunday, or an official BOT holiday."""

    # Saturday = 5, Sunday = 6 in Python's weekday()
    if check_date.weekday() >= 5:
        return True

    # Check the holidays we downloaded from the BOT API
    if check_date.strftime("%Y-%m-%d") in holidays_dict:
        return True

    # Check our hardcoded annual holidays as a backup
    if (check_date.month, check_date.day) in FIXED_HOLIDAYS:
        return True

    return False


def resolve_effective_rate_date(original_date, holidays_dict):
    """Find the most recent day the BOT was open.

    If the given date is a normal weekday, it returns that same date.
    If it is a weekend or holiday, it steps backward one day at a time
    until it finds an open day.

    Example:
      Sunday Feb 8 -> Saturday Feb 7 (closed) -> Friday Feb 6 (open!)
      Returns: Feb 6
    """
    resolved = original_date
    # Safety: never go back more than 10 days (longest Thai holiday stretch)
    for _ in range(10):
        if not is_bot_closed(resolved, holidays_dict):
            return resolved
        resolved -= timedelta(days=1)
    return resolved


def find_columns(ws):
    """Scan the first few rows of a sheet to find the column positions.

    Instead of assuming "Column I is always Cur", we read the header
    row and look for the keywords. This way the script works with any
    file layout as long as the column names match.

    Returns a dict like: {"cur": 9, "export_dt": 17, "rate_dt": 18, "ex_rate": 19}
    or None if the required columns are not found.
    """
    found = {}

    # Check the first 3 rows (some files have merged header rows)
    for row_num in range(1, 4):
        for col_num in range(1, ws.max_column + 1):
            cell_value = ws.cell(row=row_num, column=col_num).value
            if cell_value is None:
                continue

            header = str(cell_value).strip()

            # Match each column header we need
            if header == HEADER_CUR and "cur" not in found:
                found["cur"] = col_num
                found["header_row"] = row_num

            elif header == HEADER_EXPORT_DT and "export_dt" not in found:
                found["export_dt"] = col_num
                found["header_row"] = row_num

            elif header.startswith(HEADER_RATE_DT) and "rate_dt" not in found:
                found["rate_dt"] = col_num

            elif header == HEADER_EX_RATE and "ex_rate" not in found:
                found["ex_rate"] = col_num

    # We need at least the currency and export date columns to work
    if "cur" in found and "export_dt" in found:
        # If the rate columns were not found, we still try to write
        # them next to the export date column
        if "rate_dt" not in found:
            found["rate_dt"] = found["export_dt"] + 1
        if "ex_rate" not in found:
            found["ex_rate"] = found["rate_dt"] + 1
        return found

    return None


# ═══════════════════════════════════════════════════════════════
# STEP 1: FETCH DATA FROM BOT API
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("  BOT Accountant Excel Filler")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ─── Fetch holidays ──────────────────────────────────────────
# We pull holidays one year at a time (the API requires this).
print("\n[1/3] Fetching holidays from BOT API...")
holidays = {}
for year in range(START.year, END.year + 1):
    holiday_url = f"{GATEWAY}{HOL_PATH}?year={year}"
    response_data = bot_api_get(holiday_url, TOKEN_HOL)
    if response_data:
        holiday_list = response_data.get("result", {}).get("data", [])
        if isinstance(holiday_list, list):
            for entry in holiday_list:
                dt = str(entry.get("Date", "")).strip()[:10]
                nm = str(entry.get("HolidayDescription", "Holiday")).strip()
                if dt:
                    holidays[dt] = nm
    print(f"  Done: {year}")

print(f"  Total holidays loaded: {len(holidays)}")

# ─── Fetch exchange rates ────────────────────────────────────
# Loop through the full date range in 30-day chunks.
# We only store the Selling rate because that is what the
# accountant needs in Column "EX Rate".
print("\n[2/3] Fetching exchange rates from BOT API...")
rates = {}
chunk_start = START

while chunk_start <= END:
    chunk_end = min(chunk_start + timedelta(days=CHUNK), END)
    sp = chunk_start.strftime("%Y-%m-%d")
    ep = chunk_end.strftime("%Y-%m-%d")

    for currency in ("USD", "EUR"):
        rate_url = (f"{GATEWAY}{EXG_PATH}?start_period={sp}"
                    f"&end_period={ep}&currency={currency}")
        response_data = bot_api_get(rate_url, TOKEN_EXG)
        if not response_data:
            continue

        try:
            details = response_data["result"]["data"]["data_detail"]
        except (KeyError, TypeError):
            continue

        if not isinstance(details, list):
            continue

        for entry in details:
            rate_date = str(entry.get("period", "")).strip()[:10]
            if not rate_date:
                continue

            buying_tt = entry.get("buying_transfer", "")
            selling_val = entry.get("selling", "")

            if rate_date not in rates:
                rates[rate_date] = {}

            # Store as floats so Excel treats them as numbers
            try:
                rates[rate_date][currency] = {
                    "buying": float(buying_tt) if buying_tt else None,
                    "selling": float(selling_val) if selling_val else None
                }
            except (ValueError, TypeError):
                rates[rate_date][currency] = {"buying": None, "selling": None}

    chunk_start = chunk_end + timedelta(days=1)

print(f"  Total trading days loaded: {len(rates)}")


# ═══════════════════════════════════════════════════════════════
# STEP 2: OPEN AND PROCESS THE EXCEL FILE
# ═══════════════════════════════════════════════════════════════
print(f"\n[3/3] Processing: {os.path.basename(INPUT_EXCEL)}")

if not os.path.exists(INPUT_EXCEL):
    sys.exit(f"Error: File not found — {INPUT_EXCEL}")

wb = openpyxl.load_workbook(INPUT_EXCEL)

# Counters to show a summary at the end
filled_count = 0
skipped_count = 0
sheets_processed = 0

for ws in wb.worksheets:

    # Process reference sheets (like "Exrate USD", "Exrate EUR")
    # We must convert their string-based dates to Real Excel Dates so that the
    # formulas in the main sheets can correctly calculate the "closest previous date"
    if ws.title.lower().startswith("exrate"):
        print(f"  [setup] '{ws.title}' — converting strings to real dates")
        for r in range(6, ws.max_row + 1):
            cell = ws.cell(row=r, column=1)
            if cell.value and isinstance(cell.value, str):
                parsed = parse_date_string(cell.value)
                if parsed:
                    cell.value = parsed
                    cell.number_format = "DD MMM YYYY"
        continue


    # Try to find the column positions in this sheet
    cols = find_columns(ws)
    if cols is None:
        print(f"  [skip] '{ws.title}' — could not find Cur/วันที่ใบขน columns")
        continue

    # Data starts one row after the header
    data_start = cols["header_row"] + 1
    print(f"  [work] '{ws.title}' — Cur=col {cols['cur']}, "
          f"วันที่ใบขน=col {cols['export_dt']}, "
          f"rate_dt=col {cols['rate_dt']}, "
          f"EX Rate=col {cols['ex_rate']}, "
          f"data starts row {data_start}")

    sheets_processed += 1
    sheet_filled = 0

    # Process each row in the sheet
    for row_num in range(data_start, ws.max_row + 1):

        # Read the currency code (Column "Cur")
        raw_cur = ws.cell(row=row_num, column=cols["cur"]).value
        if not raw_cur:
            continue

        # Normalize to uppercase and skip anything that is not USD or EUR
        currency = str(raw_cur).strip().upper()
        if currency not in ("USD", "EUR"):
            skipped_count += 1
            continue

        # Read the export date (Column "วันที่ใบขน")
        raw_date = ws.cell(row=row_num, column=cols["export_dt"]).value
        export_date = parse_date_string(raw_date)
        if export_date is None:
            skipped_count += 1
            continue

        # Resolve the date: if it is a weekend or holiday,
        # step backwards until we find a normal trading day
        effective_date = resolve_effective_rate_date(export_date, holidays)

        # Look up the rate for this currency physically via Python (just to check if we SHOULD write a formula)
        effective_str = effective_date.strftime("%Y-%m-%d")
        day_rates = rates.get(effective_str, {})
        currency_rates = day_rates.get(currency)

        if currency_rates and (currency_rates["buying"] is not None or currency_rates["selling"] is not None):
            import openpyxl.utils
            Q_col_letter = openpyxl.utils.get_column_letter(cols["export_dt"])
            R_col_letter = openpyxl.utils.get_column_letter(cols["rate_dt"])
            ref_sheet = f"Exrate {currency}"
            
            # The user requested 'วันที่ดึง Exchange rate date' (Column R) to be a dynamic formula.
            # XLOOKUP with match_mode -1 searches for "exact match or next smaller item".
            # Since real dates are numbers under the hood, this perfectly finds the most recent past trading day!
            # Format: =XLOOKUP(lookup, lookup_array, return_array, if_not_found, match_mode)
            formula_R = f'=XLOOKUP({Q_col_letter}{row_num}, \'{ref_sheet}\'!$A$6:$A$1000, \'{ref_sheet}\'!$A$6:$A$1000, "", -1)'
            ws.cell(row=row_num, column=cols["rate_dt"]).value = formula_R
            # Format the cell to display correctly (e.g. "04 Feb 2026")
            ws.cell(row=row_num, column=cols["rate_dt"]).number_format = "DD MMM YYYY"
            
            # The 'EX Rate' (Column S) simply looks up the resolved date from R in the Exrate sheet.
            formula_S = f"=VLOOKUP({R_col_letter}{row_num},'{ref_sheet}'!$A$6:$D$1000,3,FALSE())"
            ws.cell(row=row_num, column=cols["ex_rate"]).value = formula_S

            filled_count += 1
            sheet_filled += 1
        else:
            # No rate available (date might be outside our fetch range)
            ws.cell(row=row_num, column=cols["ex_rate"]).value = None
            skipped_count += 1
            print(f"    Row {row_num}: No rate for {currency} on {effective_str}")

    print(f"    Filled {sheet_filled} rows in '{ws.title}'")


# ═══════════════════════════════════════════════════════════════
# STEP 3: SAVE THE OUTPUT FILE
# ═══════════════════════════════════════════════════════════════
wb.save(OUTPUT_EXCEL)

print(f"\n{'=' * 60}")
print(f"  DONE!")
print(f"  Sheets processed: {sheets_processed}")
print(f"  Rows filled:      {filled_count}")
print(f"  Rows skipped:     {skipped_count}")
print(f"  Output saved:     {os.path.basename(OUTPUT_EXCEL)}")
print(f"{'=' * 60}")


# ─── Changelog ───────────────────────────────────────────────
# Every update to this file gets a new entry below.
#
# 2026-03-10 | v1 — Initial version
#            | - Hardcoded to exchange_rate_file_sample.xlsx only
#            | - Column positions were fixed (I=9, Q=17, R=18, S=19)
#
# 2026-03-10 | v2 — Generalized for any Excel file
#            | - Now accepts any .xlsx file as a command-line argument
#            | - Auto-detects column positions by scanning the header row
#            | - Processes ALL data sheets (skips "Exrate" reference sheets)
#            | - Output filename mirrors input: "file_updated_YYYY-MM-DD.xlsx"
#            | - Rewrote all comments to be clearer and more human-readable
#
# 2026-03-11 | v3 — Formula & Format Overhaul
#            | - Preserves VLOOKUP formula in EX Rate column instead of static numbers
#            | - Standardized all date outputs to "DD MMM YYYY" format
#            | - Fixed output filename to use a consistent non-dated name
#            | - Added logic to skip reference sheets (per user request)
#            | - Improved log feedback and detailed setup/formula comments
#
# 2026-03-11 | v4 — Dynamic Smart Formulas
#            | - Converted Date column in Exrate reference tabs to Real Excel Dates
#            | - Upgraded "วันที่ดึง Exchange rate date" (Col R) to use XLOOKUP(..., -1)
#            |   so it automatically resolves weekend/holiday fallbacks inside Excel
