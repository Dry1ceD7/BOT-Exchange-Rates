# Excel Formula Guide for Exchange Rate File

This guide explains every formula inside `exchange_rate_file_sample.xlsx` and teaches you how to use them step by step.

---

# Part 1: Formulas Already in Your File (Original)

These are the formulas that were already typed into the original `exchange_rate_file_sample.xlsx` before any changes.

---

## Formula 1: EX Rate (Column S) — The Exchange Rate Lookup

**What it does:** Finds the exchange rate for a specific date.

**The formula in cell S2:**

```
=VLOOKUP(Q2, 'Exrate USD'!$A$7:$D$230, 3, FALSE)
```

**Breaking it down piece by piece:**

| Part | What it means |
|---|---|
| `VLOOKUP(` | "Go look up a value for me" |
| `Q2` | "Take the date from cell Q2 (วันที่ใบขน)" |
| `'Exrate USD'!$A$7:$D$230` | "Search in the 'Exrate USD' tab, from row 7 to row 230, columns A through D" |
| `3` | "When you find the date, give me the value from the 3rd column (Column C = Buying Transfer rate)" |
| `FALSE` | "The date must match EXACTLY — no guessing" |

**Where to find it in Excel:**

1. Open your file
2. Go to sheet **FEB**
3. Click on cell **S2** (the EX Rate column, row 2)
4. Look at the **formula bar** at the top — you'll see the formula there

**The problem with this formula:**

- It uses `FALSE` (exact match), so if the date in Q2 is a **Sunday**, it won't find it in the Exrate sheet and will show **#N/A error**
- Column 3 gives the **Buying Transfer** rate, but you actually want the **Selling** rate (Column 4)

---

## Formula 2: Vat Sale THB (Column L) — Calculate Baht Value

**What it does:** Converts a foreign currency amount to Thai Baht.

**The formula in cell L2:**

```
=K2*S2
```

**Breaking it down:**

| Part | What it means |
|---|---|
| `K2` | The FOB amount (the value of goods in foreign currency) |
| `*` | Multiply |
| `S2` | The exchange rate from Column S |

**Example:** If K2 = 720 USD and S2 = 31.4688, then L2 = 720 × 31.4688 = **22,657.54 Baht**

**Where to find it:**

1. Click on cell **L2** in the FEB sheet
2. Look at the formula bar — you'll see `=K2*S2`

---

## Formula 3: Difference (Column N) — Amount vs FOB

**What it does:** Calculates the difference between the total Amount and the FOB value.

**The formula in cell N2:**

```
=H2-K2
```

| Part | What it means |
|---|---|
| `H2` | The total Amount (Column H) |
| `-` | Subtract |
| `K2` | The FOB value (Column K) |

**Where to find it:**

1. Click on cell **N2** in the FEB sheet
2. Formula bar shows `=H2-K2`

---

# Part 2: The Better Way (XLOOKUP)

The old `VLOOKUP` breaks on weekends. Here is the improved version using `XLOOKUP`.

---

## Step-by-Step: How to Replace the Old Formula

### Step 1: Click on cell S2

- Open your file, go to sheet **FEB**
- Click on cell **S2** (the first EX Rate cell)

### Step 2: Delete the old formula

- Press **Delete** on your keyboard to clear the cell

### Step 3: Type the new formula

For **USD** rows, type this exactly:

```
=XLOOKUP(Q2,'Exrate USD'!$A:$A,'Exrate USD'!$D:$D,"No Data",-1)
```

Then press **Enter**.

### Step 4: Understanding what each part does

| Part | What it means in simple words |
|---|---|
| `XLOOKUP(` | "Go search for something" |
| `Q2` | "Take the date from Q2 (วันที่ใบขน)" |
| `'Exrate USD'!$A:$A` | "Search through ALL dates in the Exrate USD sheet" |
| `'Exrate USD'!$D:$D` | "When found, give me the value from Column D (Selling Rate)" |
| `"No Data"` | "If you can't find anything at all, show 'No Data'" |
| `-1` | **THIS IS THE KEY PART**: "If the exact date doesn't exist, go backwards and find the closest previous date" |

### Why `-1` solves the weekend problem

- Your date is **Sunday Feb 8** → not in the list (BOT is closed)
- Excel thinks: "Feb 8 not found... let me go back... Feb 7 (Saturday, also not found)... Feb 6 (Friday, FOUND!)"
- It returns Friday Feb 6's Selling Rate

### Step 5: Copy the formula down

1. Click on cell **S2** (where you just typed the formula)
2. Move your mouse to the **bottom-right corner** of the cell — you'll see a small **black cross (+)**
3. **Click and drag** that cross all the way down to the last row of data
4. Excel will copy the formula to every row automatically

---

## Bonus: One Formula That Handles Both USD and EUR

If your sheet has both USD and EUR rows mixed together, use this formula instead. It checks Column I (Cur) to decide which rate sheet to use:

```
=IFS(I2="USD",XLOOKUP(Q2,'Exrate USD'!$A:$A,'Exrate USD'!$D:$D,0,-1),I2="EUR",XLOOKUP(Q2,'Exrate EUR'!$A:$A,'Exrate EUR'!$D:$D,0,-1),TRUE,"")
```

| Part | What it means |
|---|---|
| `IFS(` | "Check multiple conditions" |
| `I2="USD"` | "If the currency in Column I is USD..." |
| `XLOOKUP(...)` | "...then look up the rate from the USD sheet" |
| `I2="EUR"` | "If the currency is EUR instead..." |
| `XLOOKUP(...)` | "...then look up from the EUR sheet" |
| `TRUE,""` | "For anything else (like THB), leave the cell empty" |

---

## Quick Reference: Exrate Sheet Layout

Your reference sheets (`Exrate USD` and `Exrate EUR`) look like this:

| Column A | Column B | Column C | Column D |
|---|---|---|---|
| Date | Sight Bill (Buying) | Transfer (Buying TT) | Selling Rate |
| 05 Jan 2026 | 31.1689 | 31.2596 | **31.5912** |
| 06 Jan 2026 | 31.0201 | 31.0942 | **31.4258** |

- **Column C** = Buying Transfer Rate (what the old VLOOKUP was fetching)
- **Column D** = Selling Rate (what you actually want)
