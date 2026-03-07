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

1. **Install Dependencies:**
   The `bot_excel_report.py` script requires `openpyxl`. It will attempt to install it automatically into a local `_libs` folder if it cannot find it, preventing system package conflicts.

2. **Configure API Tokens:**
   This project requires two API tokens from the Bank of Thailand.

   - Edit the `.env` file in the root directory.
   - Add or update your tokens in the following format:

     ```env
     BOT_TOKEN_EXG="your_exchange_rate_token_here"
     BOT_TOKEN_HOL="your_holiday_token_here"
     ```

   > [!TIP]
   > **How to get your Bank of Thailand API Tokens:**
   > 1. **Register:** Sign up at the [BOT Developer Portal](https://portal.api.bot.or.th/).
   > 2. **Subscribe:** Go to **Catalogues**, find the "Daily Average Exchange Rate" and "Holiday" APIs, and click **Access with this plan** for each.
   > 3. **Create App:** Click your cart icon and "Create a new app" to register these subscriptions to a project.
   > 4. **Copy Token:** Go to **Profile** > **My apps**, select your app, and you'll find the **Token** ready to copy.

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
