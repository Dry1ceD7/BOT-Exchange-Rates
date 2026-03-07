# Bank of Thailand Exchange Rate Generator

A Python toolset designed for corporate finance departments to automatically fetch, process, and format official exchange rates (USD and EUR) from the Bank of Thailand (BOT) API.

## Features
- **Accurate:** Hits the official BOT API for Weighted-Average Interbank Exchange Rates.
- **Corporate Formatting:** Generates a presentation-ready Excel workbook with multiple tabs, conditional formatting, and performance line charts.
- **Robust Accounting:** Calculates up to 4+ decimal places. Includes built-in Thai fixed holiday calendars to annotate holidays that fall on weekends.

## Prerequisites
- Python 3.7+
- Bank of Thailand API Tokens

## Setup

1. **Get your API Tokens:**
   Register at [portal.api.bot.or.th](https://portal.api.bot.or.th). You need two apps:
   - Exchange Rates API
   - Financial Institutions Holidays API

2. **Create a `.env` file:**
   Create a `.env` file in the same directory as the scripts and paste your tokens:
   ```env
   BOT_TOKEN_EXG="your_exchange_rate_token_here"
   BOT_TOKEN_HOL="your_holiday_token_here"
   ```

3. **Install Dependencies:**
   The `bot_excel_report.py` script requires `openpyxl`. It will attempt to install it automatically into a local `_libs` folder if it cannot find it, preventing system package conflicts.

## Usage

**To generate an Executive Excel Report:**
```bash
python3 bot_excel_report.py
```
*Outputs: `BOT_ExchangeRate_Report_YYYY-MM-DD.xlsx`*

**To generate a raw CSV:**
```bash
python3 bot_generator.py > BOT_Exchange_rates.csv
```
*Outputs columns: Year, Date, USD_Buying_TT, USD_Selling, EUR_Buying_TT, EUR_Selling, Remark*

## Security
- Do not commit your `.env` file. It is safely ignored by `.gitignore`.
