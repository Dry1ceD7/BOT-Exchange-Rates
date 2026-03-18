# BOT Exchange Rate Processor (v2.3.2) - Agent Memory (L1 Context)

## 🏛️ The Mission
This project is a V2.3.2 standalone, enterprise-grade Bank of Thailand (BOT) Exchange Rate Processor built for legacy financial accounting systems. It replaces a fragmented 3-script process with a single, mathematically rigorous GUI that supports batch processing, automatic backups, and zero-latency caching.

## ⚖️ The "Featherweight" Constraints
Strict zero-tolerance hardware limits:
- **Target Hardware**: Legacy office PCs with max **4GB RAM**.
- **Memory Profiling**: Strict **15MB file-size limit** on all Excel inputs.
- **Processing**: Sequential read/write. Per-file `wb.close()` + `gc.collect()` after every save.

## 🧰 The Tech Stack
- **UI Framework**: `CustomTkinter` with optional `tkinterdnd2` drag-and-drop.
- **Data Engine**: Pure `openpyxl`. In-place overwrite only. **No pandas allowed**.
- **Network Layer**: `httpx` (async) with `tenacity` exponential backoff.
- **Validation**: `Pydantic v2` for strict JSON schema enforcement.
- **Cache**: Built-in `sqlite3` (WAL mode) — `data/cache.db`.
- **Backup**: `shutil` + `os` — timestamped copies in `data/backups/`.

## 🗂️ Architecture (V2.3.2)
```
main.py              → Token validation → GUI launch
core/api_client.py   → Async BOT API client (Pydantic schemas)
core/logic.py        → Zero-guess rollback engine (5-day limit)
core/engine.py       → Orchestrator (cache-first → backup → process → gc)
core/database.py     → Thread-safe SQLite cache (holidays + rates)
core/backup_manager.py → Timestamped backup/restore/7-day cleanup
gui/app.py           → Smart Date Toggle, Drop Zone, Batch Queue, Revert
```

## 🧮 The Core Logic Rules
1. **Targeting**: Locate columns: `"วันที่ใบขน"`, `"วันที่ดึง Exchange rate date"`, `"Cur"`, `"EX Rate"`.
2. **Date Extraction**: Read date from `"วันที่ใบขน"` column.
3. **Zero-Guess Rollback**: If target date is weekend/holiday, step back 1 day at a time. **5-day max** — halts with `<ERROR: No Rate>` if exceeded.
4. **Mathematical Truth**: Writes hard-coded `Decimal` values (4dp). No Excel formulas.
5. **Cache-First**: SQLite checked before API. Today's rate cached until new data arrives.
6. **Fail-Safe Backup**: Every file backed up BEFORE `openpyxl.load_workbook()`. If backup fails, file is skipped.
7. **In-Place Editing**: Overwrites original `.xlsx`. Explicit `wb.close()` releases OS file handle immediately.

## 🔐 Security
- API tokens loaded from `.env` via `python-dotenv`.
- Early validation in `main.py` — native error popup if tokens missing.
- `.env` excluded from version control via `.gitignore`.
