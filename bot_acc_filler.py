#!/usr/bin/env python3
"""
============================================================
  BOT FINANCE ACCOUNTING FILLER (v2026)
============================================================
"""
import os
import sys
import json
import subprocess
from datetime import date, datetime, timedelta

# ─── Ensure local libraries are installed ─────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(SCRIPT_DIR, "_libs")
os.makedirs(_LIBS_DIR, exist_ok=True)
if _LIBS_DIR not in sys.path: sys.path.insert(0, _LIBS_DIR)

def ensure_libs():
    libs = ["polars", "xlsxwriter", "openpyxl"]
    for lib in libs:
        try:
            __import__(lib)
        except ImportError:
            print(f"Installing {lib} locally...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--target", _LIBS_DIR, lib, "--break-system-packages"])

ensure_libs()
import polars as pl
import xlsxwriter

# ─── Load Config ──────────────────────────────────────────────
def load_config():
    conf_path = os.path.join(SCRIPT_DIR, "config.json")
    with open(conf_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONF = load_config()
ACC_CONF = CONF["accounting"]
MASTER_DATA = os.path.join(SCRIPT_DIR, "BOT_Exchange_rates.csv")

def get_col_letter(n):
    """Convert column index (0-based) to Excel column letter."""
    string = ""
    while n >= 0:
        n, remainder = divmod(n, 26)
        string = chr(65 + remainder) + string
        n -= 1
    return string

def parse_multi_format_date(date_str):
    """Try various date formats common in Thai accounting exports."""
    if not date_str or str(date_str).lower() == "nan" or str(date_str).lower() == "null":
        return None
    
    date_str = str(date_str).strip()
    formats = [
        "%Y-%m-%d",    # 2026-02-04
        "%d-%m-%Y",    # 04-02-2026
        "%d/%m/%Y",    # 04/02/2026
        "%d %b %Y",    # 04 Feb 2026 (Common in your sample)
        "%d %B %Y",    # 04 February 2026
        "%Y%m%d",      # 20260204
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def find_latest_rate(target_date: date, rates_df: pl.DataFrame, limit: int = 5):
    """Backtracks from target_date to find the nearest preceding trade day."""
    if rates_df is None: return None, None
    for i in range(limit + 1):
        check_date = target_date - timedelta(days=i)
        check_str = check_date.strftime("%Y-%m-%d")
        match = rates_df.filter(pl.col("period") == check_str)
        if not match.is_empty() and match["selling"][0] is not None:
            return check_date, float(match["selling"][0])
    return None, None

def main():
    print("Starting Finance Accounting Filler (Sample-Ready Edition)...")
    
    # 1. Load Data
    input_path = os.path.join(SCRIPT_DIR, ACC_CONF["input_file"])
    output_path = os.path.join(SCRIPT_DIR, ACC_CONF["output_file"])
    
    if not os.path.exists(MASTER_DATA):
        print("  [Warn] BOT_Exchange_rates.csv missing. Backtracking logic will rely on Excel formulas only.")
        rates_df = None
    else:
        rates_df = pl.read_csv(MASTER_DATA)

    if not os.path.exists(input_path):
        print(f"  [Error] Input file {ACC_CONF['input_file']} not found.")
        return
    else:
        shipments = pl.read_excel(input_path, engine="openpyxl") if input_path.endswith(".xlsx") else pl.read_csv(input_path)

    print(f"  Processing {len(shipments)} rows from '{ACC_CONF['input_file']}'...")

    # 2. Build Output
    wb = xlsxwriter.Workbook(output_path)
    ws = wb.add_worksheet("Accounting Report")
    
    # Formats
    header_fmt = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1, "align": "center"})
    data_fmt = wb.add_format({"border": 1})
    num_fmt = wb.add_format({"num_format": "#,##0.0000", "border": 1})
    date_fmt = wb.add_format({"num_format": "yyyy-mm-dd", "border": 1, "align": "center"})

    # Map Headers
    headers = list(shipments.columns)
    for c in [ACC_CONF["target_date_col"], ACC_CONF["rate_col"]]:
        if c not in headers: headers.append(c)
    
    col_map = {h: i for i, h in enumerate(headers)}
    date_col_idx = col_map[ACC_CONF["date_col"]]
    target_date_col_idx = col_map[ACC_CONF["target_date_col"]]
    rate_col_idx = col_map[ACC_CONF["rate_col"]]
    ccy_col_idx = col_map.get(ACC_CONF.get("ccy_col", "Cur"), -1)

    # Write Headers
    for i, h in enumerate(headers):
        ws.write(0, i, h, header_fmt)
        ws.set_column(i, i, 22)

    # 3. Write Data & Formulas
    excel_mode = ACC_CONF.get("excel_version", "auto")

    for row_idx, row_dict in enumerate(shipments.to_dicts(), 1):
        # Write existing data
        for k, v in row_dict.items():
            ws.write(row_idx, col_map[k], v, data_fmt)

        # Build Formulas
        ref_cell = f"{get_col_letter(date_col_idx)}{row_idx + 1}"
        
        # Determine Currency for the sheet reference
        cur_val = "USD" # Default
        if ccy_col_idx != -1:
            raw_ccy = row_dict.get(ACC_CONF.get("ccy_col", "Cur"), "USD")
            if raw_ccy: cur_val = str(raw_ccy).upper().strip()
            
        sheet_ref = f"'{cur_val} Rates'"
        
        # Formula Logic
        # XLOOKUP Match Mode -1: Exact match or next smaller (Smart Backtracking)
        # DATEVALUE: Handles string dates like "04 Feb 2026"
        if excel_mode == "old":
            lookup_date_fmt = f"IFERROR(VLOOKUP(DATEVALUE({ref_cell}), {sheet_ref}!$A$3:$G$10000, 1, FALSE), \"\")"
            lookup_rate_fmt = f"IFERROR(VLOOKUP(DATEVALUE({ref_cell}), {sheet_ref}!$A$3:$G$10000, 4, FALSE), \"\")"
        else:
            lookup_date_fmt = f"IFERROR(XLOOKUP(DATEVALUE({ref_cell}), {sheet_ref}!$A$3:$A$10000, {sheet_ref}!$A$3:$A$10000, \"\", -1), \"\")"
            lookup_rate_fmt = f"IFERROR(XLOOKUP(DATEVALUE({ref_cell}), {sheet_ref}!$A$3:$A$10000, {sheet_ref}!$D$3:$D$10000, \"\", -1), \"\")"

        ws.write_formula(row_idx, target_date_col_idx, f"={lookup_date_fmt}", date_fmt)
        ws.write_formula(row_idx, rate_col_idx, f"={lookup_rate_fmt}", num_fmt)

    wb.close()
    print(f"============================================================\n  DONE!\n  Output Saved: {output_path}\n============================================================")

if __name__ == "__main__":
    main()

# ─── Changelog ───────────────────────────────────────────────
# Every major logic or visual update to this script is noted here.
#
# 2026-03-15 | v2.0.0 | v2026 Standard Upgrade
#            | - Upgraded to v2026 Standard (v2025 tech stack retained).
#            | - Added smart backtracking for weekends/holidays (max 5 days).
#            | - Integrated "Living Formulas" (IFERROR, DATEVALUE, XLOOKUP/VLOOKUP).
#            | - Added support for "DD MMM YYYY" date format and dynamic Currency columns.
