<div align="center">

# BOT Exchange Rate Processor

**Enterprise Desktop Application for Bank of Thailand Exchange Rate Automation**

Version 3.0.35  ·  Modular SFFB Architecture  ·  Cross-Platform  ·  CI/CD Release Pipeline

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-All_Rights_Reserved-red)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-140%20Passed-brightgreen)](tests/)

---

</div>

## Executive Summary

The **BOT Exchange Rate Processor** is a standalone desktop application that automates the extraction, resolution, and embedding of official Bank of Thailand (BOT) exchange rates into financial accounting ledgers (`.xlsx`).

It replaces a fragmented, error-prone multi-script workflow with a single, production-grade GUI application — built for **zero-downtime corporate environments**, legacy office hardware (4GB RAM, low-resolution monitors), and strict Thai accounting compliance.

### What's New in V3.0 (up to v3.0.20)

| Feature | Description |
|---------|-------------|
| **True Dark & Light Themes** | Dynamic recoloring of all UI elements with 28 customized color tokens for a premium aesthetic |
| **High-Speed Concurrent API** | Uses `asyncio.gather` for parallel USD & EUR fetching with 0.3s-0.8s micro-cooldowns (~3-5x faster) |
| **In-Place File Processing** | Modifies files directly with hidden background backups for zero duplication |
| **Instant One-Click Revert** | "Revert Previous Edit" button instantly unwinds the last file change using the backup manager |
| **100% Formatting Preservation** | Pure Python `.xls` fallback fully preserves all fonts, cell sizes, background colors, and borders |
| **Auto-Detect Date Range** | Opt-in toggle to automatically parse the oldest required dates directly from dropped ledgers |
| **In-App Auto-Updater** | Built-in GitHub Releases updater with progress bar and automatic installer launching |
| **Native App Icon & UI Polishing** | Crisp multi-resolution `.ico`/`.icns` embedded deeply into the OS taskbar and app windows |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          main.py                                 │
│        .env Loader → Token Validation → GUI Launch               │
│        Global Exception Handler (error.log + GUI popup)          │
├──────────────────────────────────────────────────────────────────┤
│                        gui/app.py                                │
│   CustomTkinter  ·  Dynamic Theme Module  ·  Auto-Updater        │
│   Universal Drop Zone  ·  Batch Queue  ·  Progress Bar           │
├──────────────┬───────────────────────┬───────────────────────────┤
│  gui/panels/ │   gui/handlers.py     │   core/workers/           │
│  LiveConsole │   BatchHandler        │   EventBus (thread-safe)  │
│  Settings    │   Async Bridge        │   Push/Drain Queue        │
│               │   Revert Handler      │                           │
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
│  Concurrent  │  SQLite Cache (WAL)     │  Backup + Revert        │
├──────────────┼─────────────────────────┼─────────────────────────┤
│  prescan.py  │  exrate_sheet.py        │  xls_converter.py       │
│  Date Range  │  Master ExRate Sheet    │  .xls → .xlsx Native    │
│  Scanner     │  Builder                │  (100% style kept)      │
└──────────────┴─────────────────────────┴─────────────────────────┘
```

**Design Principles**:
- **Modular SFFB** (Structure-First, File-by-File) — each concern isolated in its own module
- **Featherweight** — 15MB file-size guardrail, per-file `gc.collect()`, zero pandas dependency
- **Cache-First** — SQLite checked before BOT API; rates cached until new data arrives
- **Fail-Safe** — every file backed up before modification; safely revertible from the UI
- **OS-Aware** — Routes natively on Windows using COM, fallback on Mac/Linux seamlessly

---

## Features

### Core Processing
- **Zero-Guess Rollback Engine** — If a date falls on a weekend or BOT holiday, the engine steps back 1 day at a time (max 5 days). Automatically unpacks hidden weekend substitutions and overlays static Thai public holidays for 100% calendar accuracy.
- **Concurrent Dual Currency** — Simultaneous (async) USD and EUR rate resolution per row.
- **Decimal Precision** — All rates written as `Decimal` values quantized to 4 decimal places (Thai accounting standard).
- **Smart Date Pre-Scanner** — Scans all queued Excel files to find the oldest date, then fetches only the necessary API range.

### Desktop Application (V3.0.20)
- **API Token Registration Dialog** — License-key-style popup on first launch to enter BOT API keys.
- **Dynamic Themes** — True Light and Dark modes (`get_theme` engine deeply coloring all CTk panels).
- **Live Processing Console** — EventBus-driven, read-only terminal log with color-coded status messages.
- **Auto-Detect Date Range** — Toggle to read start dates directly from ledger files. No manual date selection needed.
- **In-App Auto-Updater** — Background Releases API check with one-click installer downloads and dispatch.
- **Drag-and-Drop Batching** — Drop individual `.xlsx` files or entire folders onto the drop zone.
- **In-Place File Processing** — Overwrites ledgers instantly in identical target paths.
- **One-Click Revert** — Restore any file instantly from its most recent timestamped backup if an error was made.

### Engine & Data Pipeline
- **Native Format Preservation** — Pure Python converter explicitly pulls `xlrd formatting_info=True` copying exact fonts (Browallia New), header colors, 4-border boundaries, and custom column widths.
- **High-Velocity Networking** — Uses `asyncio.gather` and 0.3s micro-cooldowns. Safely clamped by 10-layer 429 Retry handling with Tenacity exponential waits.
- **SQLite Cache (WAL Mode)** — Rates and holidays cached locally. Repeat runs skip the API entirely.

---

## Local Setup — Quick Start

### Prerequisites

| # | Software | Download | Notes |
|:-:|----------|----------|-------|
| 1 | **Python 3.12+** | [Download Python](https://www.python.org/downloads/) | **Windows:** check "Add Python to PATH" during install |
| 2 | **Git** | [Download Git](https://git-scm.com/downloads) | Install with default options |
| 3 | **uv** (recommended) | [Install uv](https://docs.astral.sh/uv/getting-started/installation/) | Fast Python package manager (optional — `pip` also works) |

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

## CI/CD Pipeline

The project includes a fully automated release pipeline (`.github/workflows/v3-release.yml`):

1. **Quality Gate** — Runs `ruff check` and `pytest` on every push
2. **Cross-Platform Build** — PyInstaller builds Windows `.exe` and macOS `.app` bundles
3. **GitHub Release** — Automatically creates a release with downloadable executables when a `v*` tag is pushed

```bash
# To trigger a release:
git tag v3.0.20
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

*Built for the Finance Department  ·  Bank of Thailand API  ·  V3.0.20*

</div>
