# Bank of Thailand Exchange Rate Generator

A Python toolset designed for corporate finance departments to automatically fetch, process, and format official exchange rates (USD and EUR) from the Bank of Thailand (BOT) API.

## Features

- **Authoritative Data Integration:** Synchronizes directly with the official Bank of Thailand (BOT) API to ensure precise, authoritative Weighted-Average Interbank Exchange Rates.
- **Corporate Formatting:** Generates a presentation-ready Excel workbook with multiple tabs, conditional formatting, and performance line charts.
- **Precision Financial Compliance:** Supports high-precision calculations (4+ decimal places) and incorporates a comprehensive Thai fiscal holiday calendar to ensure accurate reporting for weekend-adjusted bank settlements.
- **Ultra-Fast Performance:** Re-engineered with an **asynchronous fetching engine** (`asyncio` + `aiohttp`), enabling concurrent API requests that reduce multi-year data downloads from minutes to seconds.
- **Shared Core Module:** All scripts share a single `bot_core.py` for API access, environment variables, and constant definitions — zero code duplication.
- **Accountant Excel Filler:** Automatically fills exchange rate formulas and dates into any accountant spreadsheet, with fuzzy column detection, error highlighting, and a desktop GUI.

## File Structure

| File | Description |
| :--- | :--- |
| `bot_core.py` | Shared core module — async API client, `.env` loader, holiday calendar |
| `bot_generator.py` | Generates a raw CSV of daily exchange rates |
| `bot_excel_report.py` | Generates a 7-tab Executive Excel Report |
| `bot_acc_filler.py` | Fills exchange rate formulas into accountant spreadsheets |
| `bot_acc_filler_gui.py` | Desktop GUI for the Excel filler (CustomTkinter) |

## Prerequisites

- **Python 3.7+**  &nbsp; [![Download Python](https://img.shields.io/badge/Download-Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
- **Bank of Thailand API Tokens**

> [!NOTE]
> **Need Python?**
>
> - **Windows:** [Download](https://www.python.org/downloads/windows/) the latest installer and **IMPORTANT**: Check the box **"Add Python to PATH"** during installation.
> - **macOS:** [Download](https://www.python.org/downloads/macos/) and run the installer, or use `brew install python` if you have Homebrew.
> - **Verify:** Open your terminal and type `python3 --version` to check.

## Setup

1. **Install Dependencies:**
   All scripts auto-install their dependencies (`aiohttp`, `openpyxl`, `thefuzz`, `customtkinter`) into a local `_libs` folder if they're not already present.

2. **Configure API Tokens:**
   This project requires two API tokens from the Bank of Thailand.

   - Edit the `.env` file in the root directory.
   - Add or update your tokens in the following format:

     ```env
     BOT_TOKEN_EXG="your_exchange_rate_token_here"
     BOT_TOKEN_HOL="your_holiday_token_here"
     ```

   > [!TIP]
   > **Detailed Guide: How to get your Bank of Thailand API Tokens**
   > 1. **Register:** Sign up at the [BOT Developer Portal](https://portal.api.bot.or.th/).
   > 2. **Subscribe to Exchange Rates:** Go to **Catalogues**, find the **Exchange Rates** card and click **MORE INFO**. Scroll to the bottom and click **ACCESS WITH THIS PLAN**.
   > 3. **Subscribe to Holidays:** Go back to **Catalogues**, find the **Others** card and click **MORE INFO**. Scroll to the bottom and click **ACCESS WITH THIS PLAN**.
   > 4. **Register your App:** Click your **Cart icon** (top right) and choose **Create a new app** to link these subscriptions to a project name of your choice.
   > 5. **Copy your Token:** Go to **Profile** > **My apps**, select the app you just created, and you will find the **Token** ready to copy.

## Usage

### Executive Excel Report

```bash
# Default (Start 2025-01-01 to today)
python3 bot_excel_report.py

# Custom Period
python3 bot_excel_report.py --start 2024-01-01 --end 2024-12-31
```

*Outputs: `BOT_ExchangeRate_Report.xlsx`*

### Raw CSV Export

```bash
# Default (Start 2025-01-01 to today)
python3 bot_generator.py

# Custom Period
python3 bot_generator.py --start 2024-01-01
```

*Outputs: `BOT_Exchange_rates.csv`*

### Accountant Excel Filler

```bash
# Default sample file
python3 bot_acc_filler.py

# Custom file
python3 bot_acc_filler.py --input Feb_2026.xlsx

# Custom input and output
python3 bot_acc_filler.py --input Feb_2026.xlsx --output Feb_filled.xlsx

# Launch the desktop GUI
python3 bot_acc_filler.py --gui

# Verbose logging
python3 bot_acc_filler.py --input Feb_2026.xlsx --verbose
```

*Outputs: `<filename>_updated.xlsx`*

---

### Command Line Arguments

**`bot_generator.py` and `bot_excel_report.py`:**

| Argument | Format | Description | Default |
| :--- | :--- | :--- | :--- |
| `--start` | `YYYY-MM-DD` | The start date for the data fetch | `2025-01-01` |
| `--end` | `YYYY-MM-DD` | The end date for the data fetch | `Today` |

**`bot_acc_filler.py`:**

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--input`, `-i` | Path to the accountant's `.xlsx` file | Sample file |
| `--output`, `-o` | Output file path | `<input>_updated.xlsx` |
| `--gui` | Launch the drag-and-drop desktop GUI | — |
| `--verbose`, `-v` | Show detailed debug logging | — |
| `--silent`, `-s` | Suppress all terminal output | — |
