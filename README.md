<div align="center">

# BOT Exchange Rate Processor

**Enterprise-Grade Bank of Thailand Exchange Rate Automation**

Version 2.5.5  ·  Modular SFFB Architecture  ·  Zero-Latency Cache

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

Before you begin, make sure these two programs are installed on your computer:

| # | Software | Download | What is it? |
|:-:|----------|----------|-------------|
| 1 | ![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white) | [⬇ Download Python](https://www.python.org/downloads/) | The programming language this app runs on. **Windows users:** during installation, make sure to check the box that says ✅ **"Add Python to PATH"** — this is required. |
| 2 | ![Git](https://img.shields.io/badge/Git-Latest-F05032?logo=git&logoColor=white) | [⬇ Download Git](https://git-scm.com/downloads) | A tool used to download this project from GitHub. Just install with the default options. |

> **💡 How do I know if I already have them?**
> Open **Terminal** (macOS) or **Command Prompt** (Windows) and type:
> ```
> python3 --version
> git --version
> ```
> If you see version numbers (e.g., `Python 3.14.3`), you're good. If you see an error, install them using the links above.

---

### Step 1 — Download This Project

Open **Terminal** (macOS) or **Command Prompt** (Windows) and run:

```bash
git clone https://github.com/Dry1ceD7/BOT-Exchange-Rates.git
cd BOT-Exchange-Rates
```

This creates a folder called `BOT-Exchange-Rates` on your computer and enters it.

---

### Step 2 — Get Your BOT API Keys

You need **two free API keys** from the Bank of Thailand. Here's how:

1. Go to the BOT API Portal: **https://apiportal.bot.or.th/**
2. Click **"Sign Up"** or **"Register"** to create a free account
3. Verify your email address and log in
4. Once logged in, go to **"My Subscriptions"** or **"API Products"**
5. Subscribe to these two APIs:

| API Name | What it does |
|----------|-------------|
| ![API](https://img.shields.io/badge/Exchange_Rate-API-2EA44F?logo=bank&logoColor=white) **Daily Weighted-average Exchange Rate** | Provides official USD and EUR exchange rates |
| ![API](https://img.shields.io/badge/Holiday-API-E4405F?logo=calendar&logoColor=white) **Financial Institution Holidays** | Tells the app which days the market is closed |

6. After subscribing, find your **API Keys** on the portal (usually under "My Subscriptions" → "Show Key")
7. Copy each key — you'll need them in the next step

> **⚠️ Keep your API keys private.** They are like passwords. Never share them or post them online.

---

### Step 3 — Set Up Your API Keys

This app reads your API keys from a small text file called `.env`. Here's how to set it up:

#### 3a. Rename the template file

In the `BOT-Exchange-Rates` folder, you'll find a file called:

```
.env.example
```

**Rename it** to just:

```
.env
```

> **💡 macOS tip:** Files starting with a dot (`.`) are hidden by default. Press `Cmd + Shift + .` in Finder to show hidden files.
>
> **💡 Windows tip:** In File Explorer, click **View** → check **"Hidden items"** to see the file.

#### 3b. Open the `.env` file and paste your keys

Open the `.env` file with any text editor (Notepad, TextEdit, etc.). You'll see:

```env
BOT_TOKEN_EXG=your_exchange_rate_api_key_here
BOT_TOKEN_HOL=your_holiday_api_key_here
```

Replace the placeholder text with your **actual API keys** from Step 2. For example:

```env
BOT_TOKEN_EXG=a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuV
BOT_TOKEN_HOL=xY9z8W7v6U5t4S3r2Q1pOnMlKjIhGfEd
```

**Save and close the file.** That's it — your credentials are configured!

---

### Step 4 — Install & Run

Go back to your **Terminal** / **Command Prompt** (make sure you're still inside the `BOT-Exchange-Rates` folder) and run these commands one by one:

**macOS / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

**Windows:**

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

> **💡 What do these commands do?**
> - `python3 -m venv venv` — Creates a private workspace so the app's libraries don't interfere with your computer
> - `source venv/bin/activate` — Enters that workspace
> - `pip install -r requirements.txt` — Downloads all the libraries the app needs (one-time only)
> - `python3 main.py` — Starts the application!

---

### Step 5 — Open Your Project Folder

To quickly find your project folder in your file manager:

- **macOS:** `open .`  (type this in Terminal)
- **Windows:** `explorer .`  (type this in Command Prompt)

This pops open the folder so you can easily drag-and-drop your `.xlsx` ledger files into the app later.

---

### What Happens on First Run

When the app starts for the first time, it automatically:

1. ✅ Creates the `data/`, `data/input/`, and `data/backups/` folders
2. ✅ Checks your API keys — if they're missing or invalid, a popup will tell you exactly what's wrong
3. ✅ Sets up the local database at `data/cache.db` (this stores exchange rates so repeated runs are instant)

> **🎉 You're all set!** Drop your `.xlsx` ledger files into the app and click **"Process Batch"**.

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

*Built for the Finance Department  ·  Bank of Thailand API  ·  V2.5.5*

</div>
