<div align="center">

# BOT Exchange Rate Processor

**Enterprise-Grade Bank of Thailand Exchange Rate Automation**

Version 2.4.0  ·  Modular SFFB Architecture  ·  Zero-Latency Cache

---

</div>

## Executive Summary

The **BOT Exchange Rate Processor** is a standalone desktop application built to automate the extraction, resolution, and embedding of official Bank of Thailand (BOT) exchange rates into financial accounting ledgers (`.xlsx`).

It replaces a fragmented, error-prone 3-script workflow with a single, mathematically rigorous GUI application — designed from the ground up for **legacy office hardware** (4GB RAM, low-resolution monitors) and strict Thai accounting compliance.

### Why This Exists

| Before | After |
|--------|-------|
| 3 separate Python scripts run manually | Single click-to-process GUI |
| No error handling on weekends/holidays | 5-day zero-guess rollback engine |
| No data caching — API called every time | SQLite cache: instant repeat lookups |
| No backups — corrupted files are lost | Timestamped backups before every edit |
| Single file processing only | Batch processing via drag-and-drop |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│              Token Validation → GUI Launch                  │
├─────────────────────────────────────────────────────────────┤
│                       gui/app.py                            │
│    CustomTkinter  ·  Smart Date Toggle  ·  Drop Zone        │
│    Batch Queue  ·  Progress Bar  ·  Revert Button           │
├─────────────────────────────────────────────────────────────┤
│                      core/engine.py                         │
│          Orchestrator: Cache → Backup → Process → GC        │
├──────────────┬──────────────┬──────────────┬────────────────┤
│  api_client  │    logic     │   database   │ backup_manager │
│  Async BOT   │  Zero-Guess  │   SQLite     │  Timestamped   │
│  API + retry │  Rollback    │   Cache      │  Backup/Revert │
└──────────────┴──────────────┴──────────────┴────────────────┘
```

**Design Principles**:
- **Modular SFFB** (Single File per Feature Block) — each concern is isolated in its own module
- **Featherweight** — strict 15MB file-size guardrail, per-file `gc.collect()`, no pandas
- **Cache-First** — SQLite checked before BOT API; today's rate cached until new data arrives
- **Fail-Safe** — every file backed up before modification; if backup fails, file is skipped

---

## Features

### Core Processing
- **Zero-Guess Rollback Engine** — If a date falls on a weekend or BOT holiday, the engine steps back 1 day at a time (max 5 days) to find the exact valid trading date. No guessing, no approximation.
- **Dual Currency Support** — Simultaneous USD and EUR rate resolution per row
- **Decimal Precision** — All rates written as `Decimal` values quantized to 4 decimal places (Thai accounting standard)

### GUI
- **Smart Date Toggle** — Defaults to today's date; unlock dropdowns for historical date selection
- **Drag-and-Drop Batching** — Drop individual `.xlsx` files or entire folders onto the drop zone
- **Per-File Progress** — Real-time status updates with file-level error reporting
- **Show in Folder** — One-click reveal of processed files in the OS file manager
- **Enterprise Typography** — Zero-emoji, corporate aesthetic designed for legacy monitors

### Data Safety
- **Zero-Latency SQLite Cache** — Rates and holidays cached locally in `data/cache.db` (WAL mode). Repeat runs skip the API entirely.
- **Fail-Safe Auto-Backups** — Pristine copy saved to `data/backups/` before every file edit. 7-day auto-cleanup.
- **One-Click Revert** — Restore any corrupted file from its most recent backup via the GUI
- **OS File Unlocking** — Explicit `workbook.close()` releases the file handle immediately after save

---

## Local Setup — Quick Start

### Prerequisites

- **Python 3.10+** — [Download](https://www.python.org/downloads/) · macOS: `brew install python` · Windows: check **"Add Python to PATH"** during install
- **Git** — [Download](https://git-scm.com/downloads) · macOS: `brew install git`
- **BOT API Keys** — [Register here](https://apiportal.bot.or.th/) for Exchange Rate + Holiday keys

---

### macOS / Linux — Single-Command Setup

Copy and paste this entire block into your terminal:

```bash
# ── Clone & Enter ─────────────────────────────────────────────────────
git clone https://github.com/Dry1ceD7/BOT-Exchange-Rates.git
cd BOT-Exchange-Rates

# ── Dependency Check ──────────────────────────────────────────────────
python3 --version || { echo "❌ Python 3 is NOT installed. Download: https://www.python.org/downloads/"; exit 1; }
git --version     || { echo "❌ Git is NOT installed. Download: https://git-scm.com/downloads"; exit 1; }

# ── Virtual Environment & Dependencies ────────────────────────────────
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── API Credential Setup ─────────────────────────────────────────────
cp .env.example .env
echo ""
echo "🔑 Enter your Bank of Thailand API keys:"
read -rp "   Exchange Rate API key (BOT_TOKEN_EXG): " EXG_KEY
read -rp "   Holiday API key      (BOT_TOKEN_HOL): " HOL_KEY
sed -i '' "s/your_exchange_rate_api_key_here/$EXG_KEY/" .env
sed -i '' "s/your_holiday_api_key_here/$HOL_KEY/" .env
echo "✅ .env file configured."

# ── Launch ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Setup Complete! Launching application...            ║"
echo "╚══════════════════════════════════════════════════════════╝"
open .
python3 main.py
```

---

### Windows — Single-Command Setup

Copy and paste this entire block into **Command Prompt**:

```bat
REM ── Clone & Enter ───────────────────────────────────────────────────
git clone https://github.com/Dry1ceD7/BOT-Exchange-Rates.git
cd BOT-Exchange-Rates

REM ── Dependency Check ────────────────────────────────────────────────
python --version || (echo ❌ Python 3 is NOT installed. Download: https://www.python.org/downloads/ & exit /b 1)
git --version    || (echo ❌ Git is NOT installed. Download: https://git-scm.com/downloads & exit /b 1)

REM ── Virtual Environment & Dependencies ──────────────────────────────
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

REM ── API Credential Setup ────────────────────────────────────────────
copy .env.example .env
echo.
echo 🔑 Enter your Bank of Thailand API keys:
set /p EXG_KEY="   Exchange Rate API key (BOT_TOKEN_EXG): "
set /p HOL_KEY="   Holiday API key      (BOT_TOKEN_HOL): "
powershell -Command "(Get-Content .env) -replace 'your_exchange_rate_api_key_here','%EXG_KEY%' | Set-Content .env"
powershell -Command "(Get-Content .env) -replace 'your_holiday_api_key_here','%HOL_KEY%' | Set-Content .env"
echo ✅ .env file configured.

REM ── Launch ──────────────────────────────────────────────────────────
echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║  ✅ Setup Complete! Launching application...            ║
echo ╚══════════════════════════════════════════════════════════╝
explorer .
python main.py
```

---

### Configuration

> **⚠ IMPORTANT:** The `.env` file contains your API secrets and is **never pushed to GitHub**. If you need to reconfigure, edit `.env` directly or delete it and re-run the setup commands above.

```env
BOT_TOKEN_EXG=your_exchange_rate_api_key_here
BOT_TOKEN_HOL=your_holiday_api_key_here
```

### What Happens on First Run

The application automatically:

1. Creates `data/`, `data/input/`, and `data/backups/` directories
2. Validates your API tokens — shows a native error popup if missing
3. Initializes the SQLite cache at `data/cache.db`

---

## Project Structure

```
BOT_Exrate/
├── main.py                 Entry point + token validation
├── core/
│   ├── api_client.py       Async BOT API client (Pydantic v2)
│   ├── logic.py            Zero-guess rollback engine
│   ├── engine.py           Orchestrator (cache → backup → process)
│   ├── database.py         Thread-safe SQLite cache
│   └── backup_manager.py   Timestamped backup/restore
├── gui/
│   └── app.py              CustomTkinter enterprise GUI
├── data/
│   ├── cache.db            SQLite rate/holiday cache (auto-created)
│   └── backups/            Timestamped file backups (auto-managed)
├── .env                    API credentials (user-supplied, gitignored)
├── .env.example            Credential template
└── .gitignore              Excludes secrets, cache, and build artifacts
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| GUI | CustomTkinter | Modern Tk-based desktop UI |
| Data | openpyxl | Direct `.xlsx` read/write (no pandas) |
| Network | httpx + tenacity | Async HTTP with exponential backoff |
| Validation | Pydantic v2 | Strict BOT API schema enforcement |
| Cache | sqlite3 (built-in) | Thread-safe local rate/holiday cache |
| Backup | shutil (built-in) | Timestamped file copy/restore |
| DnD | tkinterdnd2 | Optional drag-and-drop support |

---

## License

This project is developed for internal enterprise use. All rights reserved.

---

<div align="center">

*Built for the Finance Department  ·  Bank of Thailand API  ·  V2.4.0*

</div>
