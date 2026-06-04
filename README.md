<div align="center">

# BOT Exchange Rate Processor

**Enterprise Desktop Application for Bank of Thailand Exchange Rate Automation**

Version 3.5.0  ·  Modular SFFB Architecture  ·  Cross-Platform  ·  CI/CD Release Pipeline

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-All_Rights_Reserved-red)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-1173%20Passed-brightgreen)](tests/)

---

</div>

## Executive Summary

The **BOT Exchange Rate Processor** is a standalone desktop application that automates the extraction, resolution, and embedding of official Bank of Thailand (BOT) exchange rates into financial accounting ledgers (`.xlsx`).

It replaces a fragmented, error-prone multi-script workflow with a single, production-grade GUI application — built for **zero-downtime corporate environments**, legacy office hardware (4GB RAM, low-resolution monitors), and strict Thai accounting compliance.

### What's New in V3.5.0 (Behavior Audit — 58 Findings Resolved, Thai UI, Backup Browser)

| Feature | Description |
|---------|-------------|
| **Thai / English UI** | New language toggle in Settings — the main window and all dialogs render in professional Thai or English (logs and audit CSVs stay English for compliance). |
| **Backup Browser** | Browse every automatic backup grouped by file with timestamps and restore any version by date, with an explicit confirmation preview. Revert now also confirms, previews, and supports `.xlsm`. |
| **Audit Trail (Real)** | The audit CSV now records every modified cell on both GUI and CLI runs — previously the log was an empty header. The engine owns one populated log per run and the app surfaces its path. |
| **Multi-Currency End-to-End** | Ledger rows in any cached/API currency are filled (not just USD/EUR), CSV-imported offline rates are consulted cache-first, unavailable rates write a visible `<ERROR: No Rate>` sentinel with per-file warnings. |
| **Scheduler Upgrades** | Persisted schedules actually re-arm after restart; minute-precision time picker; skip-weekends / skip-Thai-holidays toggles; missed slots within 120 min still fire; tray notification + last-run summary for minimized runs. |
| **Headless / CLI Power** | `--headless` no longer blocked by an open GUI; new `--dry-run`, `--schedule`, `--quiet` / `--verbose`; documented exit codes (0 ok / 1 total / 2 partial / 3 config / 4 nothing to do). |
| **First-Run & Token UX** | Test Connection button verifies keys before saving, pasted keys are validated/stripped, rejected keys (401/403) produce a clear "re-enter your API keys" message, keychain fallback to .env now warns. |
| **Batch UX & Feedback** | Failed files are listed with reasons on completion, the queue clears after a run, dry runs are honestly labeled, oversized/unsupported files are flagged at selection time, locked Excel files explain "close the file in Excel and retry". |
| **Accessibility & Fit** | Light-mode text meets WCAG AA contrast (≥4.5:1, regression-locked by tests), minimum window size enforced for small legacy screens. Suite grew 806 → 1173 tests. |

### What's New in V3.4.0 (Deep-Audit Hardening — 57 Findings Resolved)

| Feature | Description |
|---------|-------------|
| **Credential Hygiene** | Keychain-aware log redaction and Sentry event scrubber; new `--purge-credentials` CLI flag and uninstaller keyring purge hook remove stored tokens on demand. |
| **Resilient API Client** | HTTP 5xx responses now retry with exponential backoff instead of failing the batch immediately. |
| **Excel Write Integrity** | Weekend/holiday carry-forward capped at 10 days (fixes blank XLOOKUP cells), atomic saves via temp-file + `os.replace`, and custom date-range end (`dr_end`) honored. |
| **Batch Robustness** | Batch processing continues past per-file API errors, honors `stop_event` cancellation mid-run, and auto-recovers from a corrupted `cache.db`. |
| **Thai Date Correctness** | Buddhist-Era years auto-detected in `parse_date`; "today" resolved in Asia/Bangkok (UTC+7) regardless of PC timezone; audit-log retention policy added. |
| **GUI Consistency** | Theme-token consistency pass, live-console line cap (memory bound), update-install progress surfacing, tray/banner features gated Windows-only. |
| **Supply-Chain Gate** | `pip-audit` now blocking in CI, CVE-driven dependency locks (urllib3/idna/pillow), ruff `S` (bandit) security lint family enabled, Node 24 GitHub Actions bumps. |
| **Installer & Legal** | Uninstaller offers consented data cleanup, macOS DMG ships a proper `.app` bundle, proprietary LICENSE added. Suite grew 703 → 806 tests. |

### What's New in V3.3.0 (Security Hardening & Engine Decomposition)

| Feature | Description |
|---------|-------------|
| **IPC Hardening** | Single-instance IPC channel rebuilt on JSON + nonce/HMAC — removed pickle deserialization (RCE class) from the socket path. |
| **Updater Integrity** | Self-update now enforces a GitHub-only SSRF allowlist and mandatory SHA-256 verification of every downloaded installer. |
| **Token-Leak Closure** | API tokens can no longer reach logs: retry `before_sleep` logs only exception class + wait time, backed by a runtime redaction filter. |
| **Mathematical Truth** | All write paths (including multi-currency custom ranges) quantize to 4dp `Decimal` — float contamination eliminated. |
| **Engine Decomposition** | `engine.py` reduced to a cache-first orchestrator; write pipeline and standalone ExRate updater extracted to `core/exrate_updater.py` with a centralized disk-space guard (`core/workbook_io.py`). |
| **SafePanel Lifecycle** | Shared `SafePanel` mixin guards every panel's `after()` scheduling against post-destroy callbacks (teardown crash class removed). |
| **GUI Widget Test Lane** | New `tests/gui/` suite drives real CustomTkinter widgets behind a display-guarded fixture — suite grew 349 → 703 tests. |
| **CI Quality Gate** | `ci.yml` runs ruff + full pytest on every push/PR plus a non-blocking `pip-audit` dependency scan. |

### What's New in V3.2.8 (Enterprise UI Stabilization & Core Architecture)
- **MCP Build Warnings Resolved**: Fully suppressed PyInstaller warnings related to missing Model Context Protocol (MCP) telemetry modules (`mcp`, `fastmcp`, `mcp.server`, `pydantic_ai.mcp`) triggered by Sentry SDK integrations, ensuring a cleaner, production-ready build output.

| Feature | Description |
|---------|-------------|
| **Rate Type Persistence** | Fixed formula injection to dynamically map XLOOKUP columns based on the user's Buying/Selling/Mid rate preference. |
| **Multi-Currency Ledger Fill** | Ledger rows in any of 10 supported currencies — `USD`, `EUR`, `GBP`, `JPY`, `CNY`, `SGD`, `HKD`, `AUD`, `CHF`, `CAD` (plus `THB`, which resolves to a literal `1.0`) — are filled end-to-end: USD/EUR use the fixed `ExRate` columns, while every other supported code gets a dynamically appended `ExRate` column wired into the row's lookup formula. Any currency outside this set is left blank and surfaced as a per-file warning (never silently empty). |
| **Batch Start Date Accuracy** | Fixed prescan pipeline bug; `process_ledger` now correctly honors user-provided start dates instead of falling back to year-end defaults. |
| **Download Concurrency Guard** | Hardened the auto-updater version panel with a strict `_busy_download` mutex to prevent installer corruption on rapid double-clicks. |
| **Anomaly UI Consistency** | Added explicit `warning` styling in `LiveConsolePanel`, preventing anomaly alerts from rendering as plain `[---]`. |
| **macOS Tkinter Engine Upgrade** | Included `run.sh` to seamlessly launch via Python 3.12 (Tk 8.6+). This entirely eliminates custom UI blank-screen failures on macOS. |
| **Theme Hardening** | Removed ALL hardcoded hexadecimal colors; now completely powered by dynamic theme tokens via `theme.py` ensuring flawless Dark/Light OS transitions. |
| **Thread & Concurrency Safety** | Total coverage with `_safe_after()` guards securing UI modifications on background tasks (e.g. rate ticking, banners) from teardown sequence crashes. |
| **Python 3.9+ Universal Syntax** | Removed PEP 604 (`X \| None`) to fully support Python 3.9 production environments without compromising type definitions by adapting standard `Optional[X]`. |
| **Robust Application Stability** | Defensive programming against malformed API returns (`decimal.InvalidOperation`) added; PIL/Pillow dynamically loaded handling icon processing safely. |

### What's New in V3.1.0 (Enterprise Feature Expansion)

| Feature | Description |
|---------|-------------|
| **Rate Anomaly Guardian** | ±5% variance detector protects ledgers from API glitches — anomalous rates flagged in console and skipped |
| **Audit Trail (CSV)** | Every cell modification logged to `data/logs/Audit_Log_*.csv` with timestamp, file, currency, and anomaly flags |
| **Live Rate Ticker** | Compact USD/EUR rate display in the header bar — auto-refreshes from cache with API fallback |
| **Auto-Scheduler** | Background timer for scheduled batch processing with folder-watch and time picker (no full-PC scan) |
| **Rate Type Selector** | Choose Buying TT, Selling, Buying Sight, or Mid Rate from Settings — controls which rate the formula references |
| **Offline CSV Import** | Import BOT's official downloadable CSV into local cache for air-gapped or offline environments |
| **Headless CLI Mode** | `python main.py --headless --input ./ledgers` — run unattended via cron/Task Scheduler |
| **Multi-Currency Cache** | New `rates_multi` SQLite table supports arbitrary currencies beyond USD/EUR |

### What's New in V3.0 (up to v3.0.45)

| Feature | Description |
|---------|-------------|
| **True Dark & Light Themes** | Dynamic adaptive UI with instantaneous switching based on OS preferences |
| **DD/MM/YYYY Native** | Smart date parser automatically prioritizes and enforces standard DD/MM/YYYY formats |
| **High-Speed Concurrent API** | Uses `asyncio.gather` for parallel USD & EUR fetching with 0.3s-0.8s micro-cooldowns (~3-5x faster) |
| **Skip-If-Correct Optimizer** | Batch engine skips rows with existing IFS formulas, radically accelerating differential runs |
| **In-Place File Processing** | Modifies files directly with hidden background backups for zero duplication |
| **Instant One-Click Revert** | "Revert Previous Edit" button instantly unwinds the last file change using the backup manager |
| **100% Formatting Preservation** | Pure Python `.xls` fallback fully preserves all fonts, cell sizes, background colors, and borders |
| **Auto-Detect Date Range** | Opt-in toggle to automatically parse the oldest required dates directly from dropped ledgers |
| **In-App Auto-Updater** | Built-in GitHub Releases updater with background download and deferred installer launching |
| **Native App Icon & UI Polishing** | Crisp multi-resolution `.ico`/`.icns` embedded deeply into the OS taskbar and app windows |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          main.py                                 │
│  .env Loader → Token Validation → argparse (GUI / --headless)    │
│  Global Exception Handler (error.log + GUI popup)                │
├──────────────────────────────────────────────────────────────────┤
│                        gui/app.py                                │
│   CustomTkinter  ·  Dynamic Theme Module  ·  Auto-Updater        │
│   Universal Drop Zone  ·  Rate Ticker  ·  Scheduler Panel        │
├──────────────┬───────────────────────┬───────────────────────────┤
│  gui/panels/ │   gui/handlers.py     │   core/workers/           │
│  LiveConsole │   BatchHandler        │   EventBus (thread-safe)  │
│  Settings    │   Async Bridge        │   ThreadRegistry          │
│  RateTicker  │   Revert Handler      │                           │
│  Scheduler   │                       │                           │
├──────────────┴───────────────────────┴───────────────────────────┤
│                   core/engine.py (Orchestrator)                  │
│     Prescan → Cache → AnomalyGuard → Backup → Dispatch → GC      │
├──────────────────────────┬───────────────────────────────────────┤
│  core/exrate_updater.py  │  WorkbookWriter (ledger pipeline)     │
│  core/workbook_io.py     │  StandaloneExRateUpdater · disk guard │
├──────────────┬───────────┴─────────────┬─────────────────────────┤
│  api_client  │  logic · database       │  backup_manager         │
│  Async BOT   │  Zero-Guess Rollback    │  Timestamped            │
│  Concurrent  │  SQLite Cache (WAL)     │  Backup + Revert        │
├──────────────┼─────────────────────────┼─────────────────────────┤
│  prescan.py  │  exrate_sheet.py        │  anomaly_guard.py       │
│  Date Range  │  Master ExRate Sheet    │  ±5% Rate Guardian      │
│  Scanner     │  Builder                │  CSV Audit Logger       │
├──────────────┼─────────────────────────┼─────────────────────────┤
│  scheduler   │  csv_import.py          │  ipc · auto_updater     │
│  Auto-Timer  │  Offline BOT CSV        │  Single-Instance IPC    │
│  Folder Watch│  Fallback Importer      │  SHA-256 Self-Update    │
└──────────────┴─────────────────────────┴─────────────────────────┘
```

**Design Principles**:
- **Modular SFFB** (Structure-First, File-by-File) — each concern isolated in its own module
- **Featherweight** — 15MB file-size guardrail, per-file `gc.collect()`, zero pandas dependency
- **Cache-First** — SQLite checked before BOT API; rates cached until new data arrives
- **Fail-Safe** — every file backed up before modification; safely revertible from the UI
- **Cross-Platform** — pure `openpyxl` engine everywhere; no COM or OS-specific code paths

---

## Features

### Core Processing
- **Zero-Guess Rollback Engine** — If a date falls on a weekend or BOT holiday, the engine steps back 1 day at a time (max 10 days, then halts with `<ERROR: No Rate>`). Automatically unpacks hidden weekend substitutions and overlays static Thai public holidays for 100% calendar accuracy.
- **Concurrent Dual Currency** — Simultaneous (async) USD and EUR rate resolution per row.
- **Decimal Precision** — All rates written as `Decimal` values quantized to 4 decimal places (Thai accounting standard).
- **Smart Date Pre-Scanner** — Scans all queued Excel files to find the oldest date, then fetches only the necessary API range.

### Desktop Application (V3.1.0)
- **API Token Registration Dialog** — License-key-style popup on first launch to enter BOT API keys.
- **Dynamic Themes** — True Light and Dark modes (`get_theme` engine deeply coloring all CTk panels).
- **Live Processing Console** — EventBus-driven, read-only terminal log with color-coded status messages.
- **Auto-Detect Date Range** — Toggle to read start dates directly from ledger files. No manual date selection needed.
- **In-App Auto-Updater** — Background Releases API check with one-click installer downloads and dispatch.
- **Drag-and-Drop Batching** — Drop individual `.xlsx` files or entire folders onto the drop zone.
- **In-Place File Processing** — Overwrites ledgers instantly in identical target paths.
- **One-Click Revert** — Restore any file instantly from its most recent timestamped backup if an error was made.
- **Live Rate Ticker** — Real-time USD/EUR rate display in the header bar with auto-refresh.
- **Auto-Scheduler Panel** — Schedule daily processing with folder-watch and time picker controls.
- **Rate Type Selector** — Choose the rate type (Buying TT, Selling, Buying Sight, Mid Rate) from Settings.
- **Offline CSV Import** — Import BOT's official CSV files into local cache for offline operation.

### Engine & Data Pipeline
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
1. Creates `data/`, `data/input/`, `data/backups/`, and `data/logs/` directories
2. Validates your API keys (popup error if missing)
3. Initializes SQLite cache at `data/cache.db`
4. Checks for updates via GitHub Releases API

Drop your `.xlsx` ledger files into the app and click **"Process Batch"**.

---

## Headless CLI Mode

For automated/unattended processing via cron jobs or Task Scheduler:

```bash
# Process a specific directory of ledgers
python main.py --headless --input /path/to/ledgers/

# Process a single file with a forced start date
python main.py --headless --input ledger.xlsx --start-date 2025-01-02

# Process default input directory (data/input/)
python main.py --headless

# Preview the changes without modifying any files (dry run / simulation)
python main.py --headless --dry-run

# Quiet: print only the final summary (clean cron mail)
python main.py --headless --quiet

# Verbose: raise the console log level to DEBUG for troubleshooting
python main.py --headless --verbose

# Emit a machine-readable JSON summary to stdout (for monitoring wrappers)
python main.py --headless --json
```

Headless mode:
- Skips the GUI entirely
- Auto-detects start dates from ledger files (override with `--start-date`)
- Prints per-file progress to stdout (standalone ExRate files are labelled
  `OK (ExRate rates refreshed)` so they are distinguishable from ledger fills)
- Generates an audit trail CSV in `data/logs/` (suppressed on `--dry-run`)
- Is **not** blocked by an already-running GUI instance

### CLI flags

| Flag | Effect |
|------|--------|
| `--headless` | Run without the GUI: process files and exit. |
| `--input`, `-i PATH` | Excel file or directory to process (default: `data/input/`). |
| `--start-date`, `-s YYYY-MM-DD` | Force the rate-extraction start date (default: auto-detect). |
| `--dry-run` | Preview changes without modifying files; skips the audit log. |
| `--quiet`, `-q` | Suppress per-file lines; print only the final summary. |
| `--verbose`, `-v` | Raise the console log level to DEBUG for troubleshooting. |
| `--json` | Emit a machine-readable JSON summary (counts, errors, audit-log path) to stdout. |
| `--schedule HH:MM` | Run the auto-scheduler in the foreground (cron-friendly), firing a headless batch daily at `HH:MM`. |
| `--purge-credentials` | Delete the stored BOT API tokens from the OS keychain and exit. Invoked by the Windows uninstaller; safe to run manually to wipe saved keys. |

### Exit codes (`--headless`)

Distinct codes let cron / monitoring wrappers tell the outcomes apart instead
of a coarse success/failure:

| Code | Meaning |
|------|---------|
| `0` | All files succeeded (or the scheduler stopped cleanly). |
| `1` | Total failure — every file failed. |
| `2` | Partial failure — some succeeded, some failed. |
| `3` | Usage/config error — missing tokens, bad input path, or bad date. |
| `4` | Nothing to do — no Excel files found to process. |

### Foreground scheduler

For headless/server boxes with no GUI session, run the auto-scheduler in the
foreground (it fires a headless batch daily at the given time and blocks until
`Ctrl-C`):

```bash
python main.py --schedule 23:00 --input ./ledgers
```

Times are local machine time. `--dry-run`, `--quiet`, and `--verbose` apply to
the batches it fires.

---

## CI/CD Pipeline

Two GitHub Actions workflows:

1. **Quality Gate** (`.github/workflows/ci.yml`) — `ruff check` + the full `pytest` suite on every push and PR, plus a non-blocking `pip-audit` dependency scan
2. **Release Pipeline** (`.github/workflows/v3-release.yml`) — PyInstaller builds Windows `.exe` and macOS `.app` bundles, then publishes a GitHub Release with downloadable executables when a `v*` tag is pushed

```bash
# To trigger a release:
git tag v3.5.0
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

Proprietary. Copyright (c) 2026 AAE. All rights reserved. This project is
developed for internal enterprise use only; redistribution is not permitted.
See [LICENSE](LICENSE) for the full terms.

---

<div align="center">

*Built for the Finance Department  ·  Bank of Thailand API  ·  V3.5.0*

</div>
