<div align="center">

# BOT Exchange Rate Processor

**Enterprise Desktop Application for Bank of Thailand Exchange Rate Automation**

Version 3.0.15  ·  Modular SFFB Architecture  ·  Cross-Platform  ·  CI/CD Release Pipeline

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-All_Rights_Reserved-red)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-140%20Passed-brightgreen)](tests/)

---

</div>

## Executive Summary

The **BOT Exchange Rate Processor** is a standalone desktop application that automates the extraction, resolution, and embedding of official Bank of Thailand (BOT) exchange rates into financial accounting ledgers (`.xlsx`).

It replaces a fragmented, error-prone multi-script workflow with a single, production-grade GUI application — built for **zero-downtime corporate environments**, legacy office hardware (4GB RAM, low-resolution monitors), and strict Thai accounting compliance.

### What's New in V3.0.0

| Feature | Description |
|---------|-------------|
| **Live Processing Console** | Real-time, terminal-like log viewer inside the GUI (EventBus-driven) |
| **Auto-Detect Date Range** | Reads start dates directly from your Excel ledgers — no manual input needed |
| **Settings Modal** | Persistent user preferences (appearance, auto-update toggle, API connectivity test) |
| **Auto-Updater Engine** | Background GitHub Releases check on startup with non-intrusive notifications |
| **Cross-Platform CI/CD** | GitHub Actions pipeline builds Windows/macOS executables and creates releases automatically |
| **OS-Aware Engine Factory** | Automatic routing to COM engine (Windows) or openpyxl fallback (macOS/Linux) |
| **Native .xls Conversion** | Legacy files converted via Excel COM or LibreOffice headless — zero fidelity loss |
| **Smart Date Pre-Scanner** | Scans all queued files to find the oldest date before hitting the API |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          main.py                                 │
│        .env Loader → Token Validation → GUI Launch               │
│        Global Exception Handler (error.log + GUI popup)          │
├──────────────────────────────────────────────────────────────────┤
│                        gui/app.py                                │
│   CustomTkinter  ·  Auto-Detect Toggle  ·  Smart Date Toggle     │
│   Universal Drop Zone  ·  Batch Queue  ·  Progress Bar           │
├──────────────┬───────────────────────┬───────────────────────────┤
│  gui/panels/ │   gui/handlers.py     │   core/workers/           │
│  LiveConsole │   BatchHandler        │   EventBus (thread-safe)  │
│  Settings    │   Async Bridge        │   Push/Drain Queue        │
│  Control     │   Revert Handler      │                           │
├──────────────┴───────────────────────┴───────────────────────────┤
│                   core/engine.py (Orchestrator)                   │
│          Prescan → Cache → Backup → Dispatch → GC                │
├──────────────────────────┬───────────────────────────────────────┤
│  core/engine_factory.py  │  Platform Router                      │
│     ├ NativeExcelEngine  │  Windows 11 → COM Engine              │
│     └ FallbackExcelEngine│  macOS/Linux → openpyxl               │
├──────────────┬───────────┴─────────────┬─────────────────────────┤
│  api_client  │  logic · database       │  backup_manager         │
│  Async BOT   │  Zero-Guess Rollback    │  Timestamped            │
│  API + retry │  SQLite Cache (WAL)     │  Backup + Revert        │
├──────────────┼─────────────────────────┼─────────────────────────┤
│  prescan.py  │  exrate_sheet.py        │  xls_converter.py       │
│  Date Range  │  Master ExRate Sheet    │  .xls → .xlsx Native    │
│  Scanner     │  Builder                │  Conversion Pipeline    │
└──────────────┴─────────────────────────┴─────────────────────────┘
```

**Design Principles**:
- **Modular SFFB** (Structure-First, File-by-File) — each concern isolated in its own module, strict <200 lines per panel
- **Featherweight** — 15MB file-size guardrail, per-file `gc.collect()`, zero pandas dependency
- **Cache-First** — SQLite checked before BOT API; rates cached until new data arrives
- **Fail-Safe** — every file backed up before modification; if backup fails, file is skipped
- **OS-Aware** — `win32com.client` COM engine on Windows (primary), `openpyxl` fallback on macOS/Linux

---

## Features

### Core Processing
- **Zero-Guess Rollback Engine** — If a date falls on a weekend or BOT holiday, the engine steps back 1 day at a time (max 5 days). Automatically unpacks hidden weekend substitutions and overlays static Thai public holidays for 100% calendar accuracy.
- **Dual Currency Support** — Simultaneous USD and EUR rate resolution per row.
- **Decimal Precision** — All rates written as `Decimal` values quantized to 4 decimal places (Thai accounting standard).
- **Smart Date Pre-Scanner** — Scans all queued Excel files to find the oldest date, then fetches only the necessary API range.

### Desktop Application (V3.0.3)
- **API Token Registration Dialog** — License-key-style popup on first launch to enter BOT API keys. No manual `.env` editing needed.
- **Live Processing Console** — EventBus-driven, read-only terminal log with color-coded status messages (`[LOG]`, `[OK]`, `[ERR]`).
- **Auto-Detect Date Range** — Toggle to read start dates directly from ledger files. No manual date selection needed.
- **Settings Modal** — Persistent preferences for appearance (Dark/Light/System), auto-update toggle, API key management, and API connectivity test.
- **Auto-Updater** — Background GitHub Releases API check on startup with non-intrusive update notifications.
- **Drag-and-Drop Batching** — Drop individual `.xlsx` files or entire folders onto the drop zone (tkinterdnd2).
- **Per-File Progress** — Real-time progress bar with file-level status and error reporting.
- **One-Click Revert** — Restore any file from its most recent timestamped backup.
- **Show in Folder** — Reveal processed files in macOS Finder, Windows Explorer, or Linux file manager.
- **Enterprise Typography** — Zero-emoji, corporate-grade aesthetic designed for legacy monitors.

### Engine & Data Pipeline
- **Native COM Engine (Windows)** — `win32com.client` spawns an invisible Excel instance for 100% style/font/border preservation. Zombie-safe context manager guarantees `excel.Quit()`.
- **OS-Aware Engine Factory** — `engine_factory.py` detects `sys.platform` and routes to the correct engine automatically.
- **Zero-Fidelity-Loss .xls Conversion** — Legacy files converted via Excel COM (Windows) or LibreOffice `soffice --headless` (macOS/Linux).
- **SQLite Cache (WAL Mode)** — Rates and holidays cached locally. Repeat runs skip the API entirely.
- **Fail-Safe Backups** — Pristine copy saved to `data/backups/` before every edit. 7-day auto-cleanup.

---

## Local Setup — Quick Start

### Prerequisites

| # | Software | Download | Notes |
|:-:|----------|----------|-------|
| 1 | **Python 3.12+** | [Download Python](https://www.python.org/downloads/) | **Windows:** check "Add Python to PATH" during install |
| 2 | **Git** | [Download Git](https://git-scm.com/downloads) | Install with default options |
| 3 | **uv** (recommended) | [Install uv](https://docs.astral.sh/uv/getting-started/installation/) | Fast Python package manager (optional — `pip` also works) |

> **Already installed?** Open Terminal (macOS) or Command Prompt (Windows) and type:
> ```
> python3 --version
> git --version
> ```

---

### Step 1 — Download This Project

```bash
git clone https://github.com/Dry1ceD7/BOT-Exchange-Rates.git
cd BOT-Exchange-Rates
```

---

### Step 2 — Get Your BOT API Keys

You need **two free API keys** from the Bank of Thailand:

1. Go to **https://apiportal.bot.or.th/** and create a free account
2. Subscribe to these APIs:

| API | Purpose |
|-----|---------|
| **Daily Weighted-average Exchange Rate** | Official USD and EUR exchange rates |
| **Financial Institution Holidays** | Market closure dates |

3. Copy your API keys from "My Subscriptions"

> **Keep your API keys private.** Never share them or commit them to Git.

---

### Step 3 — Install and Run

The app will **automatically prompt you** for your API keys on first launch via a registration dialog. No manual file editing needed.

**With uv (recommended):**

```bash
uv sync
uv run python main.py
```

**With pip:**

```bash
pip install -r requirements.txt
python3 main.py    # macOS/Linux
python main.py     # Windows
```

**Windows shortcut:** Double-click the included `run.bat` file.

---

### First Run

The application automatically:
1. Creates `data/`, `data/input/`, and `data/backups/` directories
2. Validates your API keys (popup error if missing)
3. Initializes SQLite cache at `data/cache.db`
4. Checks for updates via GitHub Releases API

Drop your `.xlsx` ledger files into the app and click **"Process Batch"**.

---

## Project Structure

```
BOT-Exchange-Rates/
├── main.py                      Entry point + token validation + global error handler
├── pyproject.toml               Project metadata, dependencies, tool config
├── requirements.txt             Pip-compatible dependency list
├── uv.lock                      Lockfile for reproducible installs
├── .env.example                 API credential template
│
├── core/                        Business Logic Layer
│   ├── api_client.py            Async BOT API client (httpx + tenacity retry)
│   ├── logic.py                 Zero-guess rollback engine + rate resolution
│   ├── engine.py                Orchestrator (prescan → cache → backup → dispatch)
│   ├── engine_factory.py        OS-aware engine router (COM vs openpyxl)
│   ├── com_engine.py            Windows COM engine (win32com.client)
│   ├── exrate_sheet.py          Master ExRate sheet builder
│   ├── prescan.py               Smart date range pre-scanner
│   ├── xls_converter.py         Native .xls → .xlsx conversion pipeline
│   ├── database.py              Thread-safe SQLite cache (WAL mode)
│   ├── backup_manager.py        Timestamped backup + restore
│   ├── auto_updater.py          GitHub Releases API update checker
│   ├── config_manager.py        JSON-backed user settings persistence
│   └── workers/
│       └── event_bus.py         Thread-safe push/drain event queue
│
├── gui/                         Presentation Layer
│   ├── app.py                   Main application window (CustomTkinter)
│   ├── handlers.py              Async batch processing bridge
│   └── panels/
│       ├── live_console.py      Real-time processing log viewer
│       ├── settings_modal.py    User preferences modal
│       └── control_panel.py     Drop zone + action buttons (extracted)
│
├── tests/                       Test Suite (140 tests)
│   ├── test_api_client.py       API client contract tests
│   ├── test_engine.py           Engine orchestration tests
│   ├── test_engine_factory.py   Factory routing + contract tests
│   ├── test_exrate_sheet.py     ExRate sheet builder tests
│   ├── test_gui_workers.py      EventBus + Settings + Panel module tests
│   ├── test_logic.py            Rollback engine + rate resolution tests
│   └── test_prescan.py          Date pre-scanner tests
│
├── .github/
│   └── workflows/
│       └── v3-release.yml       CI/CD: lint → test → build → release
│
└── data/                        Runtime Data (auto-created, gitignored)
    ├── cache.db                 SQLite rate/holiday cache
    └── backups/                 Timestamped file backups
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| GUI | CustomTkinter 5.2+ | Modern Tk-based desktop UI with dark/light themes |
| Data Engine | openpyxl 3.1+ | Direct `.xlsx` read/write (zero pandas dependency) |
| COM Engine | win32com.client | Native Excel automation on Windows (style preservation) |
| Network | httpx + tenacity | Async HTTP with exponential backoff retry |
| Validation | Pydantic v2 | Strict BOT API response schema enforcement |
| Cache | sqlite3 (built-in) | Thread-safe local rate/holiday cache (WAL mode) |
| Backup | shutil (built-in) | Timestamped file copy + restore |
| DnD | tkinterdnd2 | Cross-platform drag-and-drop file support |
| Settings | JSON (built-in) | Persistent user preferences |
| Packaging | uv + PyInstaller | Dependency management + executable builds |
| CI/CD | GitHub Actions | Automated lint, test, build, and release pipeline |

---

## CI/CD Pipeline

The project includes a fully automated release pipeline (`.github/workflows/v3-release.yml`):

1. **Quality Gate** — Runs `ruff check` and `pytest` on every push
2. **Cross-Platform Build** — PyInstaller builds Windows `.exe` and macOS `.app` bundles
3. **GitHub Release** — Automatically creates a release with downloadable executables when a `v*` tag is pushed

```bash
# To trigger a release:
git tag v3.0.0
git push origin main --tags
```

---

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run linter
uv run ruff check .

# Run tests
uv run pytest tests/ -v

# Run the application
uv run python main.py
```

---

## License

This project is developed for internal enterprise use. All rights reserved.

---

<div align="center">

*Built for the Finance Department  ·  Bank of Thailand API  ·  V3.0.3*

</div>
