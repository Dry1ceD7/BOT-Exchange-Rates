#!/usr/bin/env python3
"""
gui/handlers.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) - QThread-Based Processing Handlers
---------------------------------------------------------------------------
Uses Qt's QThread + Signals for thread-safe UI updates.
"""

import asyncio
import logging
import os
from datetime import date
from typing import Dict, List, Optional

import httpx
from PySide6.QtCore import QThread, Signal

from core.api_client import BOTClient
from core.engine import LedgerEngine
from core.workers.event_bus import EventBus

logger = logging.getLogger(__name__)


class BatchWorker(QThread):
    """QThread worker for async batch processing."""

    progress = Signal(int, int, str, str)
    finished = Signal(int, int, list)
    error = Signal(str)
    log = Signal(str)

    def __init__(self, file_queue: List[str], start_date: str,
                 event_bus: Optional[EventBus] = None, parent=None):
        super().__init__(parent)
        self.file_queue = file_queue
        self.start_date = start_date
        self.bus = event_bus or EventBus()

    def run(self):
        try:
            asyncio.run(self._run_batch())
        except (httpx.ConnectError, httpx.TimeoutException):
            msg = "Network error — check your internet connection."
            self.bus.push({"type": "error", "msg": msg})
            self.error.emit(msg)
        except Exception as e:
            self.bus.push({"type": "error", "msg": str(e)})
            self.error.emit(str(e))

    async def _run_batch(self):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api, event_bus=self.bus)

            date_label = self.start_date or "auto-detect"
            self.bus.push({"type": "log", "msg": f"API connected. Date: {date_label}"})
            self.log.emit(f"API connected. Date: {date_label}")

            def progress_cb(idx, total, fname, error):
                if error:
                    self.bus.push({"type": "error", "msg": f"[{idx}/{total}] {fname} — SKIPPED: {error}"})
                    self.log.emit(f"[{idx}/{total}] {fname} — SKIPPED: {error}")
                else:
                    self.bus.push({"type": "log", "msg": f"[{idx}/{total}] {fname} — OK"})
                    self.log.emit(f"[{idx}/{total}] {fname} — OK")
                self.progress.emit(idx, total, fname, error or "")

            success, fail, errors = await engine.process_batch(
                self.file_queue, start_date=self.start_date, progress_cb=progress_cb,
            )
            self.bus.push({"type": "success", "msg": f"Batch complete: {success} OK, {fail} failed."})
            self.finished.emit(success, fail, errors)


class RevertWorker(QThread):
    """QThread worker for file revert operations."""

    success = Signal(str, str)
    error = Signal(str)
    log = Signal(str)

    def __init__(self, filepath: str, event_bus: Optional[EventBus] = None, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.bus = event_bus or EventBus()

    def run(self):
        from core.backup_manager import BackupError, BackupManager
        try:
            backup_mgr = BackupManager()
            backup_used = backup_mgr.restore_latest(self.filepath)
            backup_name = os.path.basename(backup_used)
            self.bus.push({"type": "success", "msg": f"Reverted from: {backup_name}"})
            self.success.emit(self.filepath, backup_name)
        except BackupError as e:
            self.bus.push({"type": "error", "msg": f"Revert failed: {e}"})
            self.error.emit(str(e))
        except Exception as e:
            self.bus.push({"type": "error", "msg": f"Revert failed: {e}"})
            self.error.emit(str(e))


class ExrateUpdateWorker(QThread):
    """QThread worker for standalone ExRate updates (existing file)."""

    finished = Signal(str)
    error = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        filepath: str,
        currencies: Optional[List[str]] = None,
        rate_types: Optional[dict] = None,
        date_range=None,
        event_bus: Optional[EventBus] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.filepath = filepath
        self.currencies = currencies
        self.rate_types = rate_types
        self.date_range = date_range
        self.bus = event_bus or EventBus()

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            self.bus.push({"type": "error", "msg": str(e)})
            self.error.emit(str(e))

    async def _run(self):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api, event_bus=self.bus)

            def status_cb(msg):
                self.log.emit(msg)

            result = await engine.update_exrate_standalone(
                self.filepath,
                progress_cb=status_cb,
                currencies=self.currencies,
                rate_types=self.rate_types,
                date_range=self.date_range,
            )
            self.finished.emit(result)


class StandaloneExrateWorker(QThread):
    """
    Creates a brand-new ExRate .xlsx file and populates it with
    exchange rate data from the BOT API.
    Supports custom currencies and rate types.
    """

    finished = Signal(str)     # output filepath
    error = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        output_dir: str,
        year: Optional[int] = None,
        currencies: Optional[List[str]] = None,
        rate_types: Optional[Dict[str, str]] = None,
        event_bus: Optional[EventBus] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.output_dir = output_dir
        self.year = year or date.today().year
        self.currencies = currencies
        self.rate_types = rate_types
        self.bus = event_bus or EventBus()

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            self.bus.push({"type": "error", "msg": str(e)})
            self.error.emit(str(e))

    async def _run(self):
        import openpyxl

        ccy_label = ", ".join(self.currencies) if self.currencies else "USD, EUR"
        self.log.emit(f"Creating standalone ExRate sheet for {self.year} ({ccy_label})...")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ExRate"

        filename = f"ExRate_{self.year}.xlsx"
        filepath = os.path.join(self.output_dir, filename)
        wb.save(filepath)
        wb.close()

        self.log.emit(f"Created: {filename}")
        self.log.emit("Fetching exchange rates from BOT API...")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api, event_bus=self.bus)

            def status_cb(msg):
                self.log.emit(msg)

            result = await engine.update_exrate_standalone(
                filepath,
                progress_cb=status_cb,
                currencies=self.currencies,
                rate_types=self.rate_types,
            )
            self.finished.emit(result)
