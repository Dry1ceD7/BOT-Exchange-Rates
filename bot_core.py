#!/usr/bin/env python3
"""
============================================================================
  BOT EXCHANGE RATE — SHARED CORE ENGINE (v2026)
  Single Source of Truth for Config, Holidays, and API Orchestration
============================================================================
"""
import os
import sys
import json
import ssl
import asyncio
import subprocess
import sqlite3
from datetime import date, timedelta, datetime
from typing import Dict, Any, Optional, List

# ─── Environment & Runtime Setup ───────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(SCRIPT_DIR, "_libs")
if not os.path.exists(_LIBS_DIR): os.makedirs(_LIBS_DIR)
if _LIBS_DIR not in sys.path: sys.path.insert(0, _LIBS_DIR)

def ensure_dependencies():
    """Ensure required packages are available locally."""
    libs = ["httpx", "tenacity", "openpyxl", "polars"] 
    for lib in libs:
        try:
            __import__(lib)
        except ImportError:
            print(f"  [Setup] Installing {lib} locally...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--target", _LIBS_DIR, lib])

ensure_dependencies()
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ─── Configuration & Persistence Loader ─────────────────────────────────────
def init_cache():
    """Initializes the local SQLite cache."""
    db_path = os.path.join(SCRIPT_DIR, "bot_rates_cache.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            date TEXT,
            currency TEXT,
            buying_tt REAL,
            selling REAL,
            PRIMARY KEY (date, currency)
        )
    """)
    conn.commit()
    return conn

def load_config():
    """Loads tokens from .env and settings from config.json."""
    # .env Loading
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'\"")

    # config.json Loading
    conf_path = os.path.join(SCRIPT_DIR, "config.json")
    with open(conf_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    
    return {
        "token_exg": os.environ.get("BOT_TOKEN_EXG", ""),
        "token_hol": os.environ.get("BOT_TOKEN_HOL", ""),
        "gateway": config_data["api"]["gateway_url"],
        "path_exg": config_data["api"]["exchange_rate_path"],
        "path_hol": config_data["api"]["holiday_path"],
        "max_days": config_data["api"]["max_days_per_request"],
        "currencies": config_data["currencies"],
        "fixed_hols": {tuple(map(int, k.split("-"))): v for k, v in config_data["fixed_holidays"].items()}
    }

CONF = load_config()
init_cache()

def get_cached_rates(start_date: date, end_date: date, currencies: List[str]) -> Dict[str, Dict[str, Any]]:
    """Retrieves rates from local SQLite cache."""
    db_path = os.path.join(SCRIPT_DIR, "bot_rates_cache.db")
    cached = {}
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        query = f"SELECT date, currency, buying_tt, selling FROM exchange_rates WHERE date BETWEEN ? AND ? AND currency IN ({','.join(['?']*len(currencies))})"
        cursor.execute(query, [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")] + currencies)
        for d, c, b, s in cursor.fetchall():
            if d not in cached: cached[d] = {}
            cached[d][c] = {"b": b, "s": s}
    return cached

def save_rates_to_cache(rates_map: Dict[str, Dict[str, Any]]):
    """Saves a map of period -> currency -> data to SQLite."""
    db_path = os.path.join(SCRIPT_DIR, "bot_rates_cache.db")
    data_to_insert = []
    for dt, currencies in rates_map.items():
        for ccy, vals in currencies.items():
            data_to_insert.append((dt, ccy, vals.get("b"), vals.get("s")))
    
    if data_to_insert:
        with sqlite3.connect(db_path) as conn:
            conn.executemany("INSERT OR REPLACE INTO exchange_rates VALUES (?, ?, ?, ?)", data_to_insert)
            conn.commit()

# ─── API Client (v2026 httpx Engine) ───────────────────────────────────────
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.RequestError, httpx.ConnectError, httpx.TimeoutException))
)
async def bot_fetch_json(client: httpx.AsyncClient, url: str, token: str):
    """Robust async fetch for BOT API."""
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    try:
        resp = await client.get(url, headers=headers, timeout=30.0)
        if resp.status_code == 404: return None # No data for this date range
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500: raise # Retry on server errors
        print(f"  [Warn] API Error {e.response.status_code} for {url}")
        return None

async def fetch_rates_with_cache(client: httpx.AsyncClient, start_date: date, end_date: date, currencies: List[str]) -> Dict[str, Dict[str, Any]]:
    """Smart fetcher: Cache-first, then API for missing dates."""
    # 1. Look in Cache
    all_rates = get_cached_rates(start_date, end_date, currencies)
    
    # 2. Identify missing date chunks
    missing_dates = []
    curr = start_date
    while curr <= end_date:
        ds = curr.strftime("%Y-%m-%d")
        if any(ccy not in all_rates.get(ds, {}) for ccy in currencies):
            missing_dates.append(curr)
        curr += timedelta(days=1)
    
    if not missing_dates:
        return all_rates

    # 3. Fetch missing chunks from API
    # Groups dates into continuous blocks for efficient fetching
    print(f"  [Cache] Found {len(all_rates)} days. Fetching {len(missing_dates)} missing days...")
    
    m_start = missing_dates[0]
    while m_start <= missing_dates[-1]:
        m_end = min(m_start + timedelta(days=CONF["max_days"]-1), missing_dates[-1])
        s_str, e_str = m_start.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d")
        
        chunk_rates = {}
        for ccy in currencies:
            url = f"{CONF['gateway']}{CONF['path_exg']}?start_period={s_str}&end_period={e_str}&currency={ccy}"
            try:
                data = await bot_fetch_json(client, url, CONF["token_exg"])
                if data:
                    for idx_r in data.get("result", {}).get("data", {}).get("data_detail", []):
                        dt = idx_r["period"]
                        if dt not in chunk_rates: chunk_rates[dt] = {}
                        chunk_rates[dt][ccy] = {
                            "b": float(idx_r["buying_transfer"]) if idx_r["buying_transfer"] else None,
                            "s": float(idx_r["selling"]) if idx_r["selling"] else None
                        }
            except Exception as e:
                print(f"  [Error] API Failure: {e}")
        
        # Save this chunk to cache and merge into all_rates
        if chunk_rates:
            save_rates_to_cache(chunk_rates)
            for dt, vals in chunk_rates.items():
                if dt not in all_rates: all_rates[dt] = {}
                all_rates[dt].update(vals)
        
        m_start = m_end + timedelta(days=1)
            
    return all_rates

import polars as pl

async def get_aligned_data(client: httpx.AsyncClient, start_date: date, end_date: date, currencies: List[str]):
    """Unified engine to fetch data and return as an aligned Polars DataFrame."""
    # 1. Fetch holidays and rates
    hols = await fetch_holidays(client, start_date.year, end_date.year)
    rates = await fetch_rates_with_cache(client, start_date, end_date, currencies)
    
    # 2. Build full date range
    all_dates = []
    curr = start_date
    while curr <= end_date:
        all_dates.append(curr)
        curr += timedelta(days=1)
    
    # 3. Create Aligned list
    aligned = []
    for dt in all_dates:
        ds = dt.strftime("%Y-%m-%d")
        row = {"date": dt, "year": dt.year, "month": dt.month, "day_name": dt.strftime("%A"), "remark": get_remark(dt, hols)}
        for ccy in currencies:
            cmap = rates.get(ds, {}).get(ccy, {})
            row[f"{ccy.lower()}_buy"] = cmap.get("b")
            row[f"{ccy.lower()}_sell"] = cmap.get("s")
        aligned.append(row)
    
    return pl.DataFrame(aligned)

# ─── Shared Business Logic ─────────────────────────────────────────────────
def get_remark(curr_date: date, holiday_map: Dict[str, str]) -> str:
    """Unified remark logic for parity with v1.3.0 standards."""
    ds = curr_date.strftime("%Y-%m-%d")
    h = holiday_map.get(ds, "")
    if not h:
        h = CONF["fixed_hols"].get((curr_date.month, curr_date.day), "")
    
    is_wknd = curr_date.weekday() >= 5
    if h and is_wknd: return f"{h}; Weekend"
    if h: return h
    if is_wknd: return "Weekend"
    return ""

async def fetch_holidays(client: httpx.AsyncClient, start_year: int, end_year: int) -> Dict[str, str]:
    """Fetch holidays for the entire requested period."""
    holidays = {}
    for y in range(start_year, end_year + 1):
        try:
            url = f"{CONF['gateway']}{CONF['path_hol']}?year={y}"
            data = await bot_fetch_json(client, url, CONF["token_hol"])
            if data:
                for h in data.get("result", {}).get("data", []):
                    holidays[h["Date"]] = h["HolidayDescription"]
        except Exception as e:
            print(f"  [Warn] Holiday fetch failed for {y}: {e}")
    return holidays

def validate_dates(start_str: str, end_str: str):
    """Parse and validate date range."""
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d").date()
        e = datetime.strptime(end_str, "%Y-%m-%d").date()
        if s > e: raise ValueError("Start date must be before end date.")
        return s, e
    except ValueError as e:
        sys.exit(f"Error: {e}")
