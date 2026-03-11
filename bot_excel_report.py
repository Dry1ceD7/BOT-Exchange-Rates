#!/usr/bin/env python3
"""
============================================================================
  BOT EXCHANGE RATE — EXECUTIVE EXCEL REPORT GENERATOR
  For: Accounting & Finance Department — Board Presentation
  Source: Bank of Thailand Official API (https://www.bot.or.th/)
============================================================================

  This script fetches exchange rate data from the official BOT API
  and generates a presentation-quality Excel workbook with:

    Tab 1: Cover Sheet          – Title, branding, report metadata
    Tab 2: USD Daily Rates      – Daily USD/THB with conditional formatting
    Tab 3: EUR Daily Rates      – Daily EUR/THB with conditional formatting
    Tab 4: Summary Dashboard    – Monthly averages, min/max, volatility
    Tab 5: Monthly Analysis     – Period-over-period comparison
    Tab 6: FX Calculator        – Interactive currency converter
    Tab 7: Notes & Disclaimers  – Financial disclaimers, sources

  Usage:
    python3 bot_excel_report.py

  Output:
    BOT_ExchangeRate_Report_YYYY-MM-DD.xlsx (in same directory)
============================================================================
"""

# ─── Standard Library Imports ────────────────────────────────
import sys
import json
import ssl
import os
import urllib.request

import importlib
from datetime import date, timedelta, datetime
from collections import OrderedDict

# ─── Auto-install openpyxl to local _libs folder if missing ──
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
    importlib.invalidate_caches()
    import openpyxl

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule

# ─── Load Environment Variables ─────────────────────────────
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
GATEWAY   = "https://gateway.api.bot.or.th"
EXG_PATH  = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOL_PATH  = "/financial-institutions-holidays/"
START     = date(2025, 1, 1)
END       = date.today()
CHUNK     = 30
TODAY_STR  = datetime.now().strftime("%Y-%m-%d")
# Output file — redirected to ../data/output/ if it exists
DATA_OUTPUT_DIR = os.path.join(PARENT_DIR, "data", "output")
if os.path.exists(DATA_OUTPUT_DIR):
    OUTPUT = os.path.join(DATA_OUTPUT_DIR, "BOT_ExchangeRate_Report.xlsx")
else:
    OUTPUT = os.path.join(SCRIPT_DIR, "BOT_ExchangeRate_Report.xlsx")

ssl_ctx = ssl.create_default_context()

# ─── Fixed Thai Calendar Holidays (for weekend annotation) ───
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
# DESIGN SYSTEM — Light Professional Theme
# ═══════════════════════════════════════════════════════════════

# Light color palette — clean corporate look
C_PRIMARY    = "1F4E79"   # Deep teal-blue for headers
C_ACCENT     = "2E75B6"   # Medium blue accent
C_ACCENT_LT  = "D6E4F0"   # Very light blue (header bg)
C_WHITE      = "FFFFFF"   # White
C_ROW_ALT    = "F2F7FB"   # Alternating row — pale blue
C_GOLD       = "BF8F00"   # Gold for highlights
C_GOLD_BG    = "FFF8E7"   # Light gold background
C_RED        = "C0392B"   # Negative change
C_GREEN      = "1E8449"   # Positive change
C_GREY       = "808080"   # Muted text
C_BORDER     = "B4C6E7"   # Soft blue border
C_HOLIDAY    = "FFF2CC"   # Light yellow for holidays
C_HOL_WKND   = "FCE4D6"   # Light peach for weekend+holiday
C_COVER_BG   = "F5F7FA"   # Cover background
C_COVER_TOP  = "1F4E79"   # Cover header band

# Pre-built fills
FILL_PRIMARY  = PatternFill("solid", fgColor=C_PRIMARY)
FILL_ACCENT   = PatternFill("solid", fgColor=C_ACCENT)
FILL_ACCENT_LT = PatternFill("solid", fgColor=C_ACCENT_LT)
FILL_WHITE    = PatternFill("solid", fgColor=C_WHITE)
FILL_ALT      = PatternFill("solid", fgColor=C_ROW_ALT)
FILL_GOLD_BG  = PatternFill("solid", fgColor=C_GOLD_BG)
FILL_HOLIDAY  = PatternFill("solid", fgColor=C_HOLIDAY)
FILL_HOL_WKND = PatternFill("solid", fgColor=C_HOL_WKND)
FILL_COVER_BG = PatternFill("solid", fgColor=C_COVER_BG)
FILL_COVER_TOP = PatternFill("solid", fgColor=C_COVER_TOP)

# Fonts
FONT_TITLE    = Font(name="Calibri", size=26, bold=True, color=C_PRIMARY)
FONT_SUBTITLE = Font(name="Calibri", size=13, color=C_ACCENT)
FONT_HDR      = Font(name="Calibri", size=11, bold=True, color=C_WHITE)

FONT_BODY     = Font(name="Calibri", size=10, color="333333")
FONT_BODY_B   = Font(name="Calibri", size=10, bold=True, color="333333")
FONT_SMALL    = Font(name="Calibri", size=9, color=C_GREY)
FONT_NUM      = Font(name="Calibri", size=10, color="333333")
FONT_REMARK   = Font(name="Calibri", size=9, italic=True, color=C_GREY)
FONT_NOTE     = Font(name="Calibri", size=10, color="555555")
FONT_DISCLAIMER = Font(name="Calibri", size=9, italic=True, color=C_GREY)
FONT_RED      = Font(name="Calibri", size=10, color=C_RED)
FONT_GREEN    = Font(name="Calibri", size=10, color=C_GREEN)

FONT_COVER_LBL = Font(name="Calibri", size=11, color=C_GREY)
FONT_COVER_VAL = Font(name="Calibri", size=11, bold=True, color="333333")

# Alignments
ALIGN_C  = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_L  = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_R  = Alignment(horizontal="right", vertical="center")
ALIGN_TL = Alignment(horizontal="left", vertical="top", wrap_text=True)

# Borders
THIN_BORDER = Border(
    left=Side(style="thin", color=C_BORDER),
    right=Side(style="thin", color=C_BORDER),
    top=Side(style="thin", color=C_BORDER),
    bottom=Side(style="thin", color=C_BORDER),
)
BOTTOM_ACCENT = Border(bottom=Side(style="medium", color=C_ACCENT))
NO_BORDER = Border()

# Number formats
NUM_FMT_RATE = '#,##0.0000'
NUM_FMT_PCT  = '0.00%'
NUM_FMT_AMT  = '#,##0.00'


def log(msg):
    print(msg)


def bot_get(url, token):
    """Authenticated GET to official BOT API gateway."""
    req = urllib.request.Request(url, headers={
        "Authorization": token, "accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw)
    except Exception as e:
        log(f"  API Error: {e}")
        return None


def write_cell(ws, row, col, value, font=FONT_BODY, fill=None,
               align=ALIGN_L, border=THIN_BORDER, num_fmt=None):
    """Write a styled cell."""
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font
    if fill:
        cell.fill = fill
    cell.alignment = align
    if border:
        cell.border = border
    if num_fmt:
        cell.number_format = num_fmt
    return cell


def set_col_widths(ws, widths):
    """Set column widths from a dict {col_letter: width}."""
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


# ═══════════════════════════════════════════════════════════════
# STEP 1: FETCH DATA FROM BOT API
# ═══════════════════════════════════════════════════════════════
log("=" * 60)
log("  BOT Executive Excel Report Generator")
log(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 60)

# ─── Holidays ────────────────────────────────────────────────
log("\n[1/4] Fetching holidays...")
holidays = {}
for yr in range(START.year, END.year + 1):
    data = bot_get(f"{GATEWAY}{HOL_PATH}?year={yr}", TOKEN_HOL)
    if data:
        for h in data.get("result", {}).get("data", []):
            dt = str(h.get("Date", "")).strip()[:10]
            nm = str(h.get("HolidayDescription", "Holiday")).strip()
            if dt:
                holidays[dt] = nm
        log(f"  ✓ {yr}: {len([d for d in holidays if d.startswith(str(yr))])} holidays")

# ─── Exchange Rates ──────────────────────────────────────────
log("\n[2/4] Fetching exchange rates...")
rates = {}
cs = START
while cs <= END:
    ce = min(cs + timedelta(days=CHUNK), END)
    sp, ep = cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")
    for ccy in ("USD", "EUR"):
        data = bot_get(
            f"{GATEWAY}{EXG_PATH}?start_period={sp}&end_period={ep}&currency={ccy}",
            TOKEN_EXG
        )
        if data:
            try:
                details = data["result"]["data"]["data_detail"]
            except (KeyError, TypeError):
                continue
            cnt = 0
            for row in (details if isinstance(details, list) else []):
                dt = str(row.get("period", "")).strip()[:10]
                if not dt:
                    continue
                bt = row.get("buying_transfer", "")
                sl = row.get("selling", "")
                if dt not in rates:
                    rates[dt] = {}
                rates[dt][ccy] = {
                    "buy_tt": float(bt) if bt else None,
                    "sell": float(sl) if sl else None,
                }
                cnt += 1
            log(f"  ✓ {ccy} {sp} → {ep}: {cnt} days")
    cs = ce + timedelta(days=1)

log(f"  Total days: {len(rates)}")

# ─── Build day-by-day list ───────────────────────────────────
log("\n[3/4] Building report data...")
all_days = []
cur = START
while cur <= END:
    ds = cur.strftime("%Y-%m-%d")
    hol = holidays.get(ds, "")
    if not hol:
        hol = FIXED_HOLIDAYS.get((cur.month, cur.day), "")
    is_wknd = cur.weekday() >= 5
    if is_wknd and hol:
        remark = f"Weekend; {hol}"
    elif is_wknd:
        remark = "Weekend"
    elif hol:
        remark = hol
    else:
        remark = ""

    day = rates.get(ds, {})
    all_days.append({
        "date": cur,
        "usd_buy": day.get("USD", {}).get("buy_tt"),
        "usd_sell": day.get("USD", {}).get("sell"),
        "eur_buy": day.get("EUR", {}).get("buy_tt"),
        "eur_sell": day.get("EUR", {}).get("sell"),
        "remark": remark,
    })
    cur += timedelta(days=1)


# ═══════════════════════════════════════════════════════════════
# STEP 4: BUILD THE EXCEL WORKBOOK
# ═══════════════════════════════════════════════════════════════
log("\n[4/4] Building Excel workbook...")
wb = openpyxl.Workbook()

# ─────────────────────────────────────────────────────────────
# TAB 1: COVER SHEET (Light theme)
# ─────────────────────────────────────────────────────────────
log("  → Cover Sheet...")
ws = wb.active
ws.title = "Cover"
ws.sheet_properties.tabColor = C_PRIMARY

# Light background
for r in range(1, 42):
    for c in range(1, 12):
        ws.cell(row=r, column=c).fill = FILL_COVER_BG

set_col_widths(ws, {"A": 4, "B": 28, "C": 28, "D": 22, "E": 15,
                     "F": 15, "G": 15, "H": 15, "I": 15, "J": 15, "K": 4})

# Top accent band
for c in range(1, 12):
    ws.cell(row=1, column=c).fill = FILL_COVER_TOP
    ws.cell(row=2, column=c).fill = FILL_COVER_TOP
ws.row_dimensions[1].height = 8
ws.row_dimensions[2].height = 8

# Thin gold accent line
for c in range(2, 11):
    ws.cell(row=3, column=c).fill = PatternFill("solid", fgColor=C_GOLD)
ws.row_dimensions[3].height = 3

# Title
ws.merge_cells("B6:J6")
write_cell(ws, 6, 2, "BANK OF THAILAND", FONT_TITLE, FILL_COVER_BG, ALIGN_L, NO_BORDER)
ws.row_dimensions[6].height = 40

ws.merge_cells("B8:J8")
write_cell(ws, 8, 2, "Daily Exchange Rate Report",
           Font(name="Calibri", size=18, color=C_GOLD, bold=True),
           FILL_COVER_BG, ALIGN_L, NO_BORDER)

ws.merge_cells("B9:J9")
write_cell(ws, 9, 2, "USD / THB  &  EUR / THB  —  Weighted Average Interbank Rates",
           FONT_SUBTITLE, FILL_COVER_BG, ALIGN_L, NO_BORDER)

# Thin line under title
for c in range(2, 11):
    ws.cell(row=11, column=c).border = BOTTOM_ACCENT
ws.row_dimensions[11].height = 6

# Report metadata on white cards
meta = [
    ("Report Period", f"{START.strftime('%B %d, %Y')}  —  {END.strftime('%B %d, %Y')}"),
    ("Generated On", datetime.now().strftime("%B %d, %Y at %H:%M")),
    ("Data Source", "Bank of Thailand Official API (gateway.api.bot.or.th)"),
    ("Currencies", "USD/THB, EUR/THB"),
    ("Rate Types", "Buying Transfer (TT), Selling"),
    ("Trading Days", f"{len(rates)} days"),
    ("Calendar Days", f"{len(all_days)} days"),
    ("Precision", "4+ decimal places (BOT standard)"),
]
for i, (lbl, val) in enumerate(meta):
    r = 13 + i
    ws.merge_cells(f"B{r}:C{r}")
    write_cell(ws, r, 2, f"  {lbl}:", FONT_COVER_LBL, FILL_WHITE, ALIGN_L, THIN_BORDER)
    ws.merge_cells(f"D{r}:J{r}")
    write_cell(ws, r, 4, f"  {val}", FONT_COVER_VAL, FILL_WHITE, ALIGN_L, THIN_BORDER)
    ws.row_dimensions[r].height = 24

# Disclaimer box
r = 23
for c in range(2, 11):
    ws.cell(row=r, column=c).border = BOTTOM_ACCENT
ws.row_dimensions[r].height = 6

ws.merge_cells(f"B25:J28")
disclaimer = (
    "CONFIDENTIAL — This report is prepared for internal use by the Finance & Accounting Department. "
    "Exchange rates shown are the daily weighted-average interbank rates published by the Bank of Thailand. "
    "These rates are indicative and may differ from actual transaction rates offered by commercial banks. "
    "This report should not be used as the sole basis for financial decisions."
)
write_cell(ws, 25, 2, disclaimer, FONT_DISCLAIMER, FILL_COVER_BG, ALIGN_TL, NO_BORDER)

# Footer
ws.merge_cells("B31:J31")
write_cell(ws, 31, 2, "© Bank of Thailand  |  https://www.bot.or.th/",
           Font(name="Calibri", size=9, color=C_GOLD), FILL_COVER_BG, ALIGN_C, NO_BORDER)

ws.sheet_view.showGridLines = False


# ─────────────────────────────────────────────────────────────
# TAB 2 & 3: USD / EUR DAILY RATES
# ─────────────────────────────────────────────────────────────
def build_rate_sheet(wb, ccy, tab_color, buy_key, sell_key):
    """Build a formatted daily rate sheet for a given currency."""
    log(f"  → {ccy} Daily Rates...")
    ws = wb.create_sheet(f"{ccy} Daily Rates")
    ws.sheet_properties.tabColor = tab_color

    set_col_widths(ws, {
        "A": 8, "B": 14, "C": 5,
        "D": 17, "E": 17,
        "F": 14, "G": 14,
        "H": 35,
    })

    # Title row — light blue band
    ws.merge_cells("A1:H1")
    write_cell(ws, 1, 1, f"  {ccy}/THB — Daily Weighted-Average Interbank Exchange Rates",
               Font(name="Calibri", size=14, bold=True, color=C_PRIMARY),
               FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
    ws.row_dimensions[1].height = 35

    # Sub-title
    ws.merge_cells("A2:H2")
    write_cell(ws, 2, 1, f"  Source: Bank of Thailand  |  Period: {START} to {END}  |  Generated: {TODAY_STR}",
               FONT_SMALL, FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
    ws.row_dimensions[2].height = 20

    # Header row
    headers = ["Year", "Date", "Day", "Buying TT", "Selling", "Daily Δ", "Δ %", "Remark"]
    for i, h in enumerate(headers, 1):
        write_cell(ws, 4, i, h, FONT_HDR, FILL_PRIMARY, ALIGN_C, THIN_BORDER)
    ws.row_dimensions[4].height = 28
    ws.auto_filter.ref = "A4:H4"
    ws.freeze_panes = "A5"

    # Data rows
    row = 5
    prev_sell = None
    for d in all_days:
        buy  = d[buy_key]
        sell = d[sell_key]
        rmk  = d["remark"]
        dt   = d["date"]

        # Row coloring
        if "Weekend" in rmk and ";" in rmk:
            row_fill = FILL_HOL_WKND      # Weekend + Holiday → peach
        elif rmk and "Weekend" not in rmk:
            row_fill = FILL_HOLIDAY        # Holiday only → yellow
        elif row % 2 == 0:
            row_fill = FILL_ALT            # Alternate → pale blue
        else:
            row_fill = FILL_WHITE          # Normal → white

        write_cell(ws, row, 1, dt.year, FONT_BODY, row_fill, ALIGN_C, THIN_BORDER)
        write_cell(ws, row, 2, dt, FONT_BODY, row_fill, ALIGN_C, THIN_BORDER, "DD MMM YYYY")
        write_cell(ws, row, 3, dt.strftime("%a"), FONT_SMALL, row_fill, ALIGN_C, THIN_BORDER)

        # Buying TT
        if buy is not None:
            write_cell(ws, row, 4, buy, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        else:
            write_cell(ws, row, 4, "", FONT_NUM, row_fill, ALIGN_R, THIN_BORDER)

        # Selling
        if sell is not None:
            write_cell(ws, row, 5, sell, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        else:
            write_cell(ws, row, 5, "", FONT_NUM, row_fill, ALIGN_R, THIN_BORDER)

        # Daily change & percentage (based on selling rate)
        if sell is not None and prev_sell is not None:
            delta = sell - prev_sell
            pct = delta / prev_sell if prev_sell != 0 else 0
            d_font = FONT_GREEN if delta >= 0 else FONT_RED
            write_cell(ws, row, 6, delta, d_font, row_fill, ALIGN_R, THIN_BORDER, "+0.0000;-0.0000")
            write_cell(ws, row, 7, pct, d_font, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_PCT)
        else:
            write_cell(ws, row, 6, "", FONT_BODY, row_fill, ALIGN_R, THIN_BORDER)
            write_cell(ws, row, 7, "", FONT_BODY, row_fill, ALIGN_R, THIN_BORDER)

        if sell is not None:
            prev_sell = sell

        # Remark
        if rmk and "Weekend" not in rmk:
            rmk_font = Font(name="Calibri", size=9, italic=True, color=C_RED)
        else:
            rmk_font = FONT_REMARK
        write_cell(ws, row, 8, rmk, rmk_font, row_fill, ALIGN_L, THIN_BORDER)
        ws.row_dimensions[row].height = 18
        row += 1

    # Statistics footer
    row += 1
    last_data_row = row - 2
    ws.merge_cells(f"A{row}:C{row}")
    write_cell(ws, row, 1, "  PERIOD STATISTICS", FONT_HDR, FILL_PRIMARY, ALIGN_L, THIN_BORDER)
    for c in range(4, 9):
        ws.cell(row=row, column=c).fill = FILL_PRIMARY
        ws.cell(row=row, column=c).border = THIN_BORDER

    stats = [
        ("Average", "AVERAGE"),
        ("Minimum", "MIN"),
        ("Maximum", "MAX"),
        ("Std Dev (σ)", "STDEV"),
        ("Count", "COUNT"),
    ]
    for si, (label, func) in enumerate(stats):
        sr = row + 1 + si
        ws.merge_cells(f"A{sr}:C{sr}")
        write_cell(ws, sr, 1, f"  {label}", FONT_BODY_B, FILL_GOLD_BG, ALIGN_L, THIN_BORDER)
        for ci in [4, 5]:
            col_l = get_column_letter(ci)
            if func == "COUNT":
                formula = f'={func}({col_l}5:{col_l}{last_data_row})'
                write_cell(ws, sr, ci, formula, FONT_BODY_B, FILL_GOLD_BG, ALIGN_R, THIN_BORDER, "#,##0")
            else:
                formula = f'={func}({col_l}5:{col_l}{last_data_row})'
                write_cell(ws, sr, ci, formula, FONT_BODY_B, FILL_GOLD_BG, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        for ci in [6, 7, 8]:
            write_cell(ws, sr, ci, "", FONT_BODY, FILL_GOLD_BG, ALIGN_R, THIN_BORDER)

    # ── FEATURE: Heatmap on Daily Δ column (F) ──────────────────
    delta_range = f"F5:F{last_data_row}"
    ws.conditional_formatting.add(delta_range,
        ColorScaleRule(
            start_type='num', start_value=-0.5, start_color='F4CCCC',   # Light red
            mid_type='num',   mid_value=0,      mid_color='FFFFFF',     # White
            end_type='num',   end_value=0.5,     end_color='D9EAD3',    # Light green
        )
    )

    return ws


ws_usd = build_rate_sheet(wb, "USD", "2E75B6", "usd_buy", "usd_sell")
ws_eur = build_rate_sheet(wb, "EUR", "1E8449", "eur_buy", "eur_sell")


# ─────────────────────────────────────────────────────────────
# TAB 4: SUMMARY DASHBOARD
# ─────────────────────────────────────────────────────────────
log("  → Summary Dashboard...")
ws = wb.create_sheet("Summary Dashboard")
ws.sheet_properties.tabColor = C_GOLD

set_col_widths(ws, {
    "A": 4, "B": 22, "C": 18, "D": 14, "E": 14,
    "F": 4, "G": 22, "H": 18, "I": 14, "J": 14, "K": 4
})

# Title
ws.merge_cells("A1:K1")
write_cell(ws, 1, 1, "  EXCHANGE RATE SUMMARY DASHBOARD",
           Font(name="Calibri", size=16, bold=True, color=C_PRIMARY),
           FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
ws.row_dimensions[1].height = 40

ws.merge_cells("A2:K2")
write_cell(ws, 2, 1, f"  Period: {START.strftime('%d %b %Y')} – {END.strftime('%d %b %Y')}  |  Generated: {TODAY_STR}",
           FONT_SMALL, FILL_ACCENT_LT, ALIGN_L, NO_BORDER)

# ── Prepare data for the highlight boxes ───────────────────
usd_all_sells = [d["usd_sell"] for d in all_days if d["usd_sell"] is not None]
eur_all_sells = [d["eur_sell"] for d in all_days if d["eur_sell"] is not None]

# Find latest, yesterday, high date, low date
def get_rate_info(all_days_list, sell_key):
    """Extract latest rate, previous rate, high/low with dates."""
    trading = [(d["date"], d[sell_key]) for d in all_days_list if d[sell_key] is not None]
    if not trading:
        return {}
    latest_date, latest_rate = trading[-1]
    prev_date, prev_rate = trading[-2] if len(trading) >= 2 else (None, None)
    first_date, first_rate = trading[0]
    all_rates = [r for _, r in trading]
    high_rate = max(all_rates)
    low_rate = min(all_rates)
    high_date = next(d for d, r in trading if r == high_rate)
    low_date = next(d for d, r in trading if r == low_rate)
    avg_rate = sum(all_rates) / len(all_rates) if all_rates else 0
    ytd_change = latest_rate - first_rate
    ytd_pct = (ytd_change / first_rate * 100) if first_rate else 0
    daily_chg = (latest_rate - prev_rate) if prev_rate else 0
    daily_pct = (daily_chg / prev_rate * 100) if prev_rate else 0
    return {
        "latest_rate": latest_rate, "latest_date": latest_date,
        "prev_rate": prev_rate, "prev_date": prev_date,
        "high_rate": high_rate, "high_date": high_date,
        "low_rate": low_rate, "low_date": low_date,
        "avg_rate": avg_rate, "trading_days": len(trading),
        "first_rate": first_rate, "first_date": first_date,
        "ytd_change": ytd_change, "ytd_pct": ytd_pct,
        "daily_chg": daily_chg, "daily_pct": daily_pct,
    }

usd_info = get_rate_info(all_days, "usd_sell")
eur_info = get_rate_info(all_days, "eur_sell")

# ── Styling ────────────────────────────────────────────────
HIGHLIGHT_FILL = PatternFill("solid", fgColor="EBF5FB")
HL_BORDER = Border(
    bottom=Side(style="thin", color=C_BORDER),
    top=Side(style="thin", color=C_BORDER),
    left=Side(style="thin", color=C_BORDER),
    right=Side(style="thin", color=C_BORDER),
)
FONT_BIG_NUM = Font(name="Calibri", size=18, bold=True, color=C_PRIMARY)
FONT_CHG_UP = Font(name="Calibri", size=11, bold=True, color=C_GREEN)
FONT_CHG_DN = Font(name="Calibri", size=11, bold=True, color=C_RED)
FONT_DATE_SM = Font(name="Calibri", size=9, italic=True, color=C_GREY)

def build_overview_box(ws, start_col, info, ccy, header_color):
    """Build one overview card for a currency."""
    c1 = start_col      # Label column (merged)
    c2 = start_col + 1  
    c3 = start_col + 2  # Value column (merged)
    c4 = start_col + 3

    eur_fill = PatternFill("solid", fgColor=header_color)

    # Header row
    ws.merge_cells(start_row=4, start_column=c1, end_row=4, end_column=c4)
    write_cell(ws, 4, c1, f"  {ccy} / THB", FONT_HDR, eur_fill, ALIGN_L, HL_BORDER)
    for c in range(c1, c4 + 1):
        ws.cell(row=4, column=c).fill = eur_fill
        ws.cell(row=4, column=c).border = HL_BORDER

    if not info:
        return

    # Row 5: Latest Rate (BIG)
    ws.merge_cells(start_row=5, start_column=c1, end_row=5, end_column=c2)
    write_cell(ws, 5, c1, "  Latest Selling Rate", FONT_BODY_B, HIGHLIGHT_FILL, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=5, start_column=c3, end_row=5, end_column=c4)
    write_cell(ws, 5, c3, info["latest_rate"], FONT_BIG_NUM, HIGHLIGHT_FILL, ALIGN_R, HL_BORDER, NUM_FMT_RATE)
    ws.row_dimensions[5].height = 32

    # Row 6: vs Yesterday — with arrow
    chg = info["daily_chg"]
    pct = info["daily_pct"]
    arrow = "▲" if chg >= 0 else "▼"
    chg_font = FONT_CHG_UP if chg >= 0 else FONT_CHG_DN
    chg_text = f"{arrow} {abs(chg):.4f}  ({abs(pct):.2f}%)"

    ws.merge_cells(start_row=6, start_column=c1, end_row=6, end_column=c2)
    write_cell(ws, 6, c1, f"  vs {info['prev_date'].strftime('%d %b') if info.get('prev_date') else 'N/A'}",
               FONT_BODY_B, HIGHLIGHT_FILL, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=6, start_column=c3, end_row=6, end_column=c4)
    write_cell(ws, 6, c3, chg_text, chg_font, HIGHLIGHT_FILL, ALIGN_R, HL_BORDER)
    ws.row_dimensions[6].height = 22

    # Row 7: Period High
    ws.merge_cells(start_row=7, start_column=c1, end_row=7, end_column=c2)
    write_cell(ws, 7, c1, f"  Period High  ({info['high_date'].strftime('%d %b')})",
               FONT_BODY_B, FILL_WHITE, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=7, start_column=c3, end_row=7, end_column=c4)
    write_cell(ws, 7, c3, info["high_rate"], FONT_NUM, FILL_WHITE, ALIGN_R, HL_BORDER, NUM_FMT_RATE)
    ws.row_dimensions[7].height = 22

    # Row 8: Period Low
    ws.merge_cells(start_row=8, start_column=c1, end_row=8, end_column=c2)
    write_cell(ws, 8, c1, f"  Period Low  ({info['low_date'].strftime('%d %b')})",
               FONT_BODY_B, FILL_WHITE, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=8, start_column=c3, end_row=8, end_column=c4)
    write_cell(ws, 8, c3, info["low_rate"], FONT_NUM, FILL_WHITE, ALIGN_R, HL_BORDER, NUM_FMT_RATE)
    ws.row_dimensions[8].height = 22

    # Row 9: Period Average
    ws.merge_cells(start_row=9, start_column=c1, end_row=9, end_column=c2)
    write_cell(ws, 9, c1, "  Period Average", FONT_BODY_B, FILL_WHITE, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=9, start_column=c3, end_row=9, end_column=c4)
    write_cell(ws, 9, c3, info["avg_rate"], FONT_NUM, FILL_WHITE, ALIGN_R, HL_BORDER, NUM_FMT_RATE)
    ws.row_dimensions[9].height = 22

    # Row 10: Trading Range (High − Low)
    t_range = info["high_rate"] - info["low_rate"]
    ws.merge_cells(start_row=10, start_column=c1, end_row=10, end_column=c2)
    write_cell(ws, 10, c1, "  Trading Range (H−L)", FONT_BODY_B, FILL_WHITE, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=10, start_column=c3, end_row=10, end_column=c4)
    write_cell(ws, 10, c3, t_range, FONT_NUM, FILL_WHITE, ALIGN_R, HL_BORDER, NUM_FMT_RATE)
    ws.row_dimensions[10].height = 22

    # Row 11: YTD Change
    ytd_arrow = "▲" if info["ytd_change"] >= 0 else "▼"
    ytd_font = FONT_CHG_UP if info["ytd_change"] >= 0 else FONT_CHG_DN
    ytd_text = f"{ytd_arrow} {abs(info['ytd_change']):.4f}  ({abs(info['ytd_pct']):.2f}%)"

    ws.merge_cells(start_row=11, start_column=c1, end_row=11, end_column=c2)
    write_cell(ws, 11, c1, f"  YTD Change  ({info['first_date'].strftime('%d %b')} → today)",
               FONT_BODY_B, HIGHLIGHT_FILL, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=11, start_column=c3, end_row=11, end_column=c4)
    write_cell(ws, 11, c3, ytd_text, ytd_font, HIGHLIGHT_FILL, ALIGN_R, HL_BORDER)
    ws.row_dimensions[11].height = 22

    # Row 12: Trading Days Count
    ws.merge_cells(start_row=12, start_column=c1, end_row=12, end_column=c2)
    write_cell(ws, 12, c1, "  Total Trading Days", FONT_BODY_B, HIGHLIGHT_FILL, ALIGN_L, HL_BORDER)
    ws.merge_cells(start_row=12, start_column=c3, end_row=12, end_column=c4)
    write_cell(ws, 12, c3, info["trading_days"], FONT_BODY_B, HIGHLIGHT_FILL, ALIGN_R, HL_BORDER, "#,##0")
    ws.row_dimensions[12].height = 22


build_overview_box(ws, 2, usd_info, "USD", "1F4E79")
build_overview_box(ws, 7, eur_info, "EUR", "1E8449")

# ── Build monthly aggregates ──────────────────────────────
monthly = OrderedDict()
for d in all_days:
    mkey = d["date"].strftime("%Y-%m")
    if mkey not in monthly:
        monthly[mkey] = {"usd_buys": [], "usd_sells": [],
                         "eur_buys": [], "eur_sells": []}
    if d["usd_buy"] is not None:
        monthly[mkey]["usd_buys"].append(d["usd_buy"])
        monthly[mkey]["usd_sells"].append(d["usd_sell"])
    if d["eur_buy"] is not None:
        monthly[mkey]["eur_buys"].append(d["eur_buy"])
        monthly[mkey]["eur_sells"].append(d["eur_sell"])

# ── Monthly Summary Tables ────────────────────────────────
# USD Section
r = 14
ws.merge_cells(f"B{r}:E{r}")
write_cell(ws, r, 2, "  USD / THB — Monthly Summary", FONT_HDR, FILL_PRIMARY, ALIGN_L, THIN_BORDER)
for c in range(2, 6):
    ws.cell(row=r, column=c).fill = FILL_PRIMARY
    ws.cell(row=r, column=c).border = THIN_BORDER

r += 1
for i, h in enumerate(["Month", "Avg Buying TT", "Avg Selling", "Spread"], 2):
    write_cell(ws, r, i, h, FONT_HDR, FILL_ACCENT, ALIGN_C, THIN_BORDER)

r += 1
for mkey, mdata in monthly.items():
    row_fill = FILL_ALT if r % 2 == 0 else FILL_WHITE
    write_cell(ws, r, 2, mkey, FONT_BODY_B, row_fill, ALIGN_C, THIN_BORDER)
    if mdata["usd_buys"]:
        avg_buy = sum(v for v in mdata["usd_buys"] if v) / len([v for v in mdata["usd_buys"] if v]) if any(mdata["usd_buys"]) else 0
        avg_sell = sum(v for v in mdata["usd_sells"] if v) / len([v for v in mdata["usd_sells"] if v]) if any(mdata["usd_sells"]) else 0
        spread = avg_sell - avg_buy if avg_buy and avg_sell else 0
        write_cell(ws, r, 3, avg_buy, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 4, avg_sell, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 5, spread, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
    r += 1

# EUR Section
ws.merge_cells(f"G14:J14")
write_cell(ws, 14, 7, "  EUR / THB — Monthly Summary", FONT_HDR,
           PatternFill("solid", fgColor="1E8449"), ALIGN_L, THIN_BORDER)
for c in range(7, 11):
    ws.cell(row=14, column=c).fill = PatternFill("solid", fgColor="1E8449")
    ws.cell(row=14, column=c).border = THIN_BORDER

for i, h in enumerate(["Month", "Avg Buying TT", "Avg Selling", "Spread"], 7):
    write_cell(ws, 15, i, h, FONT_HDR, FILL_ACCENT, ALIGN_C, THIN_BORDER)

er = 16
for mkey, mdata in monthly.items():
    row_fill = FILL_ALT if er % 2 == 0 else FILL_WHITE
    write_cell(ws, er, 7, mkey, FONT_BODY_B, row_fill, ALIGN_C, THIN_BORDER)
    if mdata["eur_buys"]:
        avg_buy = sum(v for v in mdata["eur_buys"] if v) / len([v for v in mdata["eur_buys"] if v]) if any(mdata["eur_buys"]) else 0
        avg_sell = sum(v for v in mdata["eur_sells"] if v) / len([v for v in mdata["eur_sells"] if v]) if any(mdata["eur_sells"]) else 0
        spread = avg_sell - avg_buy if avg_buy and avg_sell else 0
        write_cell(ws, er, 8, avg_buy, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, er, 9, avg_sell, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, er, 10, spread, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
    er += 1

# ── Charts: USD + EUR side by side ────────────────────────
chart_start = max(r, er) + 2

# USD Chart
usd_chart = LineChart()
usd_chart.title = "USD/THB Selling Rate Trend"
usd_chart.style = 10
usd_chart.y_axis.title = "THB per 1 USD"
usd_chart.x_axis.title = "Month"
usd_chart.height = 14
usd_chart.width = 14
usd_chart.y_axis.numFmt = NUM_FMT_RATE

usd_data_end = 15 + len(monthly)
data_ref = Reference(ws, min_col=4, min_row=15, max_row=usd_data_end)
cats_ref = Reference(ws, min_col=2, min_row=16, max_row=usd_data_end)
usd_chart.add_data(data_ref, titles_from_data=True)
usd_chart.set_categories(cats_ref)
usd_chart.series[0].graphicalProperties.line.width = 25000
ws.add_chart(usd_chart, f"B{chart_start}")

# EUR Chart
eur_chart = LineChart()
eur_chart.title = "EUR/THB Selling Rate Trend"
eur_chart.style = 10
eur_chart.y_axis.title = "THB per 1 EUR"
eur_chart.x_axis.title = "Month"
eur_chart.height = 14
eur_chart.width = 14
eur_chart.y_axis.numFmt = NUM_FMT_RATE

eur_data_end = 15 + len(monthly)
eur_data_ref = Reference(ws, min_col=9, min_row=15, max_row=eur_data_end)
eur_cats_ref = Reference(ws, min_col=7, min_row=16, max_row=eur_data_end)
eur_chart.add_data(eur_data_ref, titles_from_data=True)
eur_chart.set_categories(eur_cats_ref)
eur_chart.series[0].graphicalProperties.line.width = 25000
eur_chart.series[0].graphicalProperties.line.solidFill = "1E8449"
ws.add_chart(eur_chart, f"G{chart_start}")

ws.sheet_view.showGridLines = False


# ─────────────────────────────────────────────────────────────
# TAB 5: MONTHLY ANALYSIS
# ─────────────────────────────────────────────────────────────
log("  → Monthly Analysis...")
ws = wb.create_sheet("Monthly Analysis")
ws.sheet_properties.tabColor = "8E44AD"

set_col_widths(ws, {
    "A": 4, "B": 16, "C": 12, "D": 16, "E": 16, "F": 16,
    "G": 16, "H": 16, "I": 16, "J": 4
})

ws.merge_cells("A1:I1")
write_cell(ws, 1, 1, "  MONTHLY RATE ANALYSIS — PERIOD COMPARISON",
           Font(name="Calibri", size=14, bold=True, color=C_PRIMARY),
           FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
ws.row_dimensions[1].height = 35

headers = ["Month", "Trading Days", "USD Open", "USD Close",
           "USD Δ", "EUR Open", "EUR Close", "EUR Δ"]
for i, h in enumerate(headers, 2):
    write_cell(ws, 3, i, h, FONT_HDR, FILL_PRIMARY, ALIGN_C, THIN_BORDER)

r = 4
for mkey, mdata in monthly.items():
    row_fill = FILL_ALT if r % 2 == 0 else FILL_WHITE
    write_cell(ws, r, 2, mkey, FONT_BODY_B, row_fill, ALIGN_C, THIN_BORDER)
    trading = max(len(mdata["usd_sells"]), len(mdata["eur_sells"]))
    write_cell(ws, r, 3, trading, FONT_BODY, row_fill, ALIGN_C, THIN_BORDER)

    if mdata["usd_sells"]:
        usd_open = mdata["usd_sells"][0]
        usd_close = mdata["usd_sells"][-1]
        usd_delta = usd_close - usd_open
        d_font = FONT_GREEN if usd_delta >= 0 else FONT_RED
        write_cell(ws, r, 4, usd_open, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 5, usd_close, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 6, usd_delta, d_font, row_fill, ALIGN_R, THIN_BORDER, "+0.0000;-0.0000")

    if mdata["eur_sells"]:
        eur_open = mdata["eur_sells"][0]
        eur_close = mdata["eur_sells"][-1]
        eur_delta = eur_close - eur_open
        d_font = FONT_GREEN if eur_delta >= 0 else FONT_RED
        write_cell(ws, r, 7, eur_open, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 8, eur_close, FONT_NUM, row_fill, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)
        write_cell(ws, r, 9, eur_delta, d_font, row_fill, ALIGN_R, THIN_BORDER, "+0.0000;-0.0000")

    ws.row_dimensions[r].height = 20
    r += 1

ws.auto_filter.ref = "B3:I3"
ws.freeze_panes = "B4"


# ─────────────────────────────────────────────────────────────
# TAB 6: FX CALCULATOR
# ─────────────────────────────────────────────────────────────
log("  → FX Calculator...")
ws = wb.create_sheet("FX Calculator")
ws.sheet_properties.tabColor = C_GOLD

for r in range(1, 30):
    for c in range(1, 10):
        ws.cell(row=r, column=c).fill = FILL_WHITE

set_col_widths(ws, {"A": 4, "B": 22, "C": 20, "D": 4, "E": 4,
                     "F": 22, "G": 20, "H": 4, "I": 4})

ws.merge_cells("A1:H1")
write_cell(ws, 1, 1, "  FX CURRENCY CALCULATOR",
           Font(name="Calibri", size=16, bold=True, color=C_PRIMARY),
           FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
ws.row_dimensions[1].height = 40

ws.merge_cells("A2:H2")
write_cell(ws, 2, 1, "  Enter amounts in the yellow cells — results calculate automatically",
           FONT_SMALL, FILL_ACCENT_LT, ALIGN_L, NO_BORDER)

# Get latest rates
latest_usd, latest_eur = None, None
for d in reversed(all_days):
    if d["usd_buy"] and not latest_usd:
        latest_usd = d
    if d["eur_buy"] and not latest_eur:
        latest_eur = d
    if latest_usd and latest_eur:
        break

INPUT_FILL = PatternFill("solid", fgColor="FFF9C4")
RESULT_FILL = PatternFill("solid", fgColor="E8F5E9")
BIG_FONT = Font(name="Calibri", size=12, bold=True, color="333333")

# USD Converter
ws.merge_cells("B4:C4")
write_cell(ws, 4, 2, "  USD ⇄ THB", FONT_HDR, FILL_PRIMARY, ALIGN_L, THIN_BORDER)

write_cell(ws, 6, 2, "Rate Date:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 6, 3, latest_usd["date"] if latest_usd else "", FONT_BODY, FILL_WHITE, ALIGN_C, THIN_BORDER, "DD MMM YYYY")

write_cell(ws, 7, 2, "Buying TT:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 7, 3, latest_usd["usd_buy"] if latest_usd else "", FONT_NUM, FILL_WHITE, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)

write_cell(ws, 8, 2, "Selling:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 8, 3, latest_usd["usd_sell"] if latest_usd else "", FONT_NUM, FILL_WHITE, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)

write_cell(ws, 10, 2, "Enter USD:", FONT_BODY_B, INPUT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 10, 3, 1000, BIG_FONT, INPUT_FILL, ALIGN_R, THIN_BORDER, NUM_FMT_AMT)

write_cell(ws, 12, 2, "= THB (Buy TT):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 12, 3, "=C10*C7", Font(name="Calibri", size=12, bold=True, color=C_GREEN),
           RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 13, 2, "= THB (Selling):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 13, 3, "=C10*C8", Font(name="Calibri", size=12, bold=True, color=C_RED),
           RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 15, 2, "Enter THB:", FONT_BODY_B, INPUT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 15, 3, 100000, BIG_FONT, INPUT_FILL, ALIGN_R, THIN_BORDER, "#,##0")

write_cell(ws, 17, 2, "= USD (Buy TT):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 17, 3, "=C15/C7", BIG_FONT, RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 18, 2, "= USD (Selling):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 18, 3, "=C15/C8", BIG_FONT, RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

# EUR Converter
ws.merge_cells("F4:G4")
write_cell(ws, 4, 6, "  EUR ⇄ THB", FONT_HDR,
           PatternFill("solid", fgColor="1E8449"), ALIGN_L, THIN_BORDER)

write_cell(ws, 6, 6, "Rate Date:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 6, 7, latest_eur["date"] if latest_eur else "", FONT_BODY, FILL_WHITE, ALIGN_C, THIN_BORDER, "DD MMM YYYY")

write_cell(ws, 7, 6, "Buying TT:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 7, 7, latest_eur["eur_buy"] if latest_eur else "", FONT_NUM, FILL_WHITE, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)

write_cell(ws, 8, 6, "Selling:", FONT_BODY_B, FILL_WHITE, ALIGN_L, THIN_BORDER)
write_cell(ws, 8, 7, latest_eur["eur_sell"] if latest_eur else "", FONT_NUM, FILL_WHITE, ALIGN_R, THIN_BORDER, NUM_FMT_RATE)

write_cell(ws, 10, 6, "Enter EUR:", FONT_BODY_B, INPUT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 10, 7, 1000, BIG_FONT, INPUT_FILL, ALIGN_R, THIN_BORDER, NUM_FMT_AMT)

write_cell(ws, 12, 6, "= THB (Buy TT):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 12, 7, "=G10*G7", Font(name="Calibri", size=12, bold=True, color=C_GREEN),
           RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 13, 6, "= THB (Selling):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 13, 7, "=G10*G8", Font(name="Calibri", size=12, bold=True, color=C_RED),
           RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 15, 6, "Enter THB:", FONT_BODY_B, INPUT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 15, 7, 100000, BIG_FONT, INPUT_FILL, ALIGN_R, THIN_BORDER, "#,##0")

write_cell(ws, 17, 6, "= EUR (Buy TT):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 17, 7, "=G15/G7", BIG_FONT, RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

write_cell(ws, 18, 6, "= EUR (Selling):", FONT_BODY_B, RESULT_FILL, ALIGN_L, THIN_BORDER)
write_cell(ws, 18, 7, "=G15/G8", BIG_FONT, RESULT_FILL, ALIGN_R, THIN_BORDER, "#,##0.00")

ws.sheet_view.showGridLines = False


# ─────────────────────────────────────────────────────────────
# TAB 7: NOTES & DISCLAIMERS
# ─────────────────────────────────────────────────────────────
log("  → Notes & Disclaimers...")
ws = wb.create_sheet("Notes & Disclaimers")
ws.sheet_properties.tabColor = C_GREY

set_col_widths(ws, {"A": 4, "B": 85, "C": 4})

ws.merge_cells("A1:B1")
write_cell(ws, 1, 1, "  NOTES, DISCLAIMERS & DATA SOURCES",
           Font(name="Calibri", size=14, bold=True, color=C_PRIMARY),
           FILL_ACCENT_LT, ALIGN_L, NO_BORDER)
ws.row_dimensions[1].height = 35
ws.cell(row=1, column=3).fill = FILL_ACCENT_LT

sections = [
    ("DATA SOURCE", [
        "All exchange rate data is sourced exclusively from the Bank of Thailand (BOT) Official API.",
        "API Gateway: https://gateway.api.bot.or.th/",
        "API Portal: https://portal.api.bot.or.th/",
        "Rates are the daily weighted-average interbank exchange rates in Bangkok.",
    ]),
    ("RATE DEFINITIONS", [
        "Buying Transfer (TT): Rate at which banks buy foreign currency via telegraphic transfer.",
        "Selling Rate: Rate at which banks sell foreign currency to customers.",
        "Spread: Difference between Selling and Buying TT rates (bank's gross margin).",
    ]),
    ("PRECISION & ACCURACY", [
        "All exchange rates are displayed with 4–7 decimal places as provided by the BOT.",
        "No rounding or modification has been applied to the original BOT data.",
        f"This report was generated on {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}.",
    ]),
    ("HOLIDAYS & NON-TRADING DAYS", [
        "Weekend days (Saturday & Sunday) are marked and have no exchange rate data.",
        "Thai public holidays are sourced from the BOT Financial Institutions Holidays API.",
        "Fixed annual holidays are annotated even when they fall on weekends.",
        "Substitution holidays (วันหยุดชดเชย) from the Royal Gazette are included via the BOT API.",
    ]),
    ("FINANCIAL DISCLAIMER", [
        "IMPORTANT: These are INDICATIVE interbank rates and may differ from actual transaction rates.",
        "Commercial banks may apply their own margins, fees, and spreads.",
        "This report is for INTERNAL USE by the Finance & Accounting Department only.",
        "Past exchange rates do not guarantee or predict future rates.",
    ]),
    ("REGULATORY COMPLIANCE", [
        "Complies with Thai accounting standards (TAS/TFRS) for foreign currency translation.",
        "Under TAS 21 (IAS 21), spot rates at transaction date should be used for initial recognition.",
        "Closing rates at reporting date should be used for monetary items at period end.",
    ]),
    ("AUTOMATION SCRIPT LOGIC", [
        "This report is automatically generated using a Python script ('bot_excel_report.py').",
        "It fetches the latest historical rates and holiday data directly from the Bank of Thailand APIs.",
        "The script computes the Daily Δ (change) and Δ % automatically.",
        "It then compiles all trading days and non-trading days (weekends/holidays) into this formatted workbook.",
    ]),
    ("UNDERSTANDING DAILY Δ (DELTA)", [
        "The Greek letter Δ (Delta) is the universal shorthand for 'change' or 'difference'.",
        "Daily Δ Formula: [Today's Rate] - [Previous Trading Day's Rate]",
        "Example: If Tuesday's rate is 34.10 and Wednesday's is 34.15, the Daily Δ is +0.05",
        "Δ % Formula: ( [Daily Δ] / [Previous Trading Day's Rate] ) * 100",
        "Example: (0.05 / 34.10) * 100 = a +0.14% change compared to the previous day.",
    ]),
]

r = 3
for title, items in sections:
    write_cell(ws, r, 2, title, Font(name="Calibri", size=12, bold=True, color=C_PRIMARY),
               FILL_WHITE, ALIGN_L, BOTTOM_ACCENT)
    ws.row_dimensions[r].height = 28
    r += 1
    for item in items:
        if "IMPORTANT:" in item:
            write_cell(ws, r, 2, item, Font(name="Calibri", size=10, bold=True, color=C_RED),
                       FILL_WHITE, ALIGN_TL, NO_BORDER)
        else:
            write_cell(ws, r, 2, f"  •  {item}", FONT_NOTE, FILL_WHITE, ALIGN_TL, NO_BORDER)
        ws.row_dimensions[r].height = 22
        r += 1
    r += 1

# Footer
ws.merge_cells(f"A{r+1}:B{r+1}")
write_cell(ws, r + 1, 1,
           f"  Report generated by BOT Excel Report Generator  |  © {datetime.now().year}  |  Bank of Thailand Data",
           Font(name="Calibri", size=9, color=C_GOLD), FILL_ACCENT_LT, ALIGN_C, NO_BORDER)

ws.sheet_view.showGridLines = False

# ═══════════════════════════════════════════════════════════════

# SAVE THE WORKBOOK
# ═══════════════════════════════════════════════════════════════
wb.save(OUTPUT)
log("")
log("=" * 60)
log("  EXCEL REPORT GENERATED SUCCESSFULLY")
log(f"  File: {OUTPUT}")
log(f"  Tabs: {len(wb.sheetnames)}")
log(f"  Sheets: {', '.join(wb.sheetnames)}")
log("=" * 60)

# ─── Changelog ───────────────────────────────────────────────
# Every major logic or visual update to this script is noted here.
#
# 2026-03-10 | v1 — Initial Dashboard version
#            | - Added Summary Dashboard with High/Low/Average metrics
#            | - Added visual heatmaps for daily changes
#            | - Added line charts for USD/EUR trends
#
# 2026-03-11 | v2 — Overhaul
#            | - Fixed log() function (switched from pass to print)
#            | - Standardized all Excel date formats to "DD MMM YYYY"
#            | - Fixed output filename to BOT_ExchangeRate_Report.xlsx
#            | - Improved code documentation and section summaries
