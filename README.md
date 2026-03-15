# Bank of Thailand Exchange Rate Tools — v1.3.9

A robust, professional-grade Python suite for fetching, reporting, and automating Bank of Thailand (BOT) exchange rate data.

## 🚀 Features
- **Authoritative Data Integration:** Synchronizes directly with the official Bank of Thailand (BOT) API to ensure precise, authoritative Weighted-Average Interbank Exchange Rates.
- **SQLite Persistence & Resilience:** Leverages a local persistence layer to cache rates, ensuring 100% data density even during API throttling (429 errors).
- **Hardened for Production**: Re-engineered logic for holiday-aware cache validation, restoring full historical data density (~300+ days per year).
- **Corporate Formatting:** Generates a presentation-ready Excel workbook with multiple tabs, conditional formatting, and performance line charts.
- **Accounting Automation**: Includes integrated support for `bot_acc_filler.py` to automatically process customs entry and accounting samples.
- **Ultra-Fast & Stable Performance**: Re-engineered with an **asynchronous fetching engine** (`asyncio` + `aiohttp`) and **concurrency throttling** (`asyncio.Semaphore`).
- **Standardized for Portability**: PC-agnostic design with automated local library management (`_libs/`).
- **Legacy Parity**: Maintains 100% visual and functional alignment with the official GitHub v1.3.0 standards.

## 🛠 Prerequisites
- Python 3.10+
- BOT API Tokens (set in `.env`)
- Required libraries are automatically installed locally in `_libs/`.

## 📦 Suite Components
| File | Description |
| :--- | :--- |
| `bot_excel_report.py` | Executive Excel Report generator with charts and formatting |
| `bot_generator.py` | Raw CSV generator and main execution entry point |
| `bot_acc_filler.py` | (v1.3.9) Accounting & Customs entry automation tool |
| `bot_core.py` | (v1.3.9) Shared logic engine and persistence manager |
| `config.json` | Centralized API, currency, and holiday configuration |
| `.env` | API token configuration (Local only, excluded from git) |

## 📝 Usage
```bash
# 1. Generate master data (2025 to present)
python3 bot_generator.py --start 2025-01-01

# 2. Generate Executive Excel Report
python3 bot_excel_report.py --start 2025-01-01

# 3. Fill Accounting sample (automatically triggered by generator if configured)
python3 bot_acc_filler.py
```

## 📅 Changelog (v1.3.9)
- **Robustness**: Implemented holiday-aware cache validation to prevent data gaps.
- **Portability**: Standardized for "Other PC" simulation with zero-config library management.
- **Formatting**: Updated CSV date formatting to `dd_mm_yyyy`.
- **Integration**: `bot_acc_filler.py` is now a first-class citizen in the main workflow.
