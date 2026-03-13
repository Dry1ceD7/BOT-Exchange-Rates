# Manual vs. Automated Exchange Rate Processing

This document explains the logic required to fill out the `exchange_rate_file_sample.xlsx` accounting sheet, both how a human would do it manually and how our upcoming Python script will automate it.

---

## 1. How to Process the Excel File Manually

If you had to do this without the bot, here is the exact step-by-step process your accountant would follow using the official dataset:

1. **Open the File:** Open your accounting file (`exchange_rate_file_sample.xlsx`) and go to the target month's sheet (e.g., "FEB").
2. **Review Row by Row:** Starting from the first row of data (Row 2):
3. **Identify Currency:** Check Column I ("Cur") to see if the transaction is `USD` or `EUR`.
4. **Identify Base Date:** Check Column Q ("วันที่ใบขน") to find the original Export Entry Date.
5. **Determine Valid Trading Date:**
   - Look at a calendar and the official BOT holiday list.
   - If the date in Column Q falls on a weekend (Saturday/Sunday) or a BOT public holiday, you must mentally roll backward day-by-day.
   - *Example:* If the date in Column Q is Sunday, Feb 8th, the Bank of Thailand is closed. You must look back to Saturday, Feb 7th (also closed), and finally land on Friday, Feb 6th. Friday becomes your valid trading date.
6. **Look Up the Rate:** Go to the Bank of Thailand website (or your downloaded `BOT_Exchange_rates.csv` file) and find the **Selling** rate (not the Buying rate) for the specific currency (USD/EUR) on that specific valid trading date.
7. **Fill in the Sheet:**
   - Type the valid trading date you found into Column R ("วันที่ดึง Exchange rate date").
   - Type the Selling rate you found into Column S ("EX Rate").
8. **Repeat:** Do this for every single transaction row in the sheet.

---

## 2. How the Automated Python Code Will Work

When we implement this feature, the Python code will use the `openpyxl` library to mimic these exact manual steps instantly and automatically.

### Step 1: Loading the Data

Instead of human eyes, the Python code will load the Excel file into memory:

```python
import openpyxl

# Load the accountant's spreadsheet
wb = openpyxl.load_workbook("exchange_rate_file_sample.xlsx")
ws = wb["FEB"] # We will target the active month's sheet
```

### Step 2: Date Resolution Logic (`resolve_effective_rate_date`)

The bot will use a dedicated function to automate "Step 5" from above.

- **How it works:** It takes the date from Column Q. It uses Python's `datetime` library to check `date.weekday()`. If the result is 5 (Saturday) or 6 (Sunday), or if the date exists in the `holidays` dictionary we download from the BOT API, it subtracts 1 day (`timedelta(days=-1)`) and checks again.
- It repeats this loop until it lands on a standard working day.

### Step 3: Iterating Through Rows

The bot will use a `for` loop to go through every row automatically:

```python
# Start at row 2 and go to the bottom of the sheet
for row in range(2, ws.max_row + 1):
    currency = ws.cell(row=row, column=9).value    # Column I (Cur)
    export_date = ws.cell(row=row, column=17).value # Column Q (วันที่ใบขน)
    
    # Skip rows that are empty or not USD/EUR
    if currency not in ["USD", "EUR"] or not export_date:
        continue 

    # Use the resolution logic to find the valid trading day
    effective_date = resolve_effective_rate_date(export_date, holidays)

    # Write the adjusted date to Column R
    ws.cell(row=row, column=18).value = effective_date.strftime("%d %b %Y")
```

### Step 4: Fetching the Rate from the BOT Data

Instead of looking at a website, the bot will look at the `rates` dictionary it just downloaded from the BOT API.

```python
    # Look up the Selling rate in the dictionary we built
    selling_rate = rates[effective_date.strftime("%Y-%m-%d")][currency]["sell"]

    # Write the rate to Column S
    ws.cell(row=row, column=19).value = selling_rate
```

### Step 5: Saving the Output

Finally, it will save the changes to a new file without any human typing:

```python
# Save as a new file so we never overwrite the original by accident!
wb.save("exchange_rate_file_sample_updated.xlsx")
```

This entirely replaces the manual search, copy, and paste process securely and accurately!
