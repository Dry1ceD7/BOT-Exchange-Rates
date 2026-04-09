#!/usr/bin/env python3
"""
gui/handlers.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor - Async Processing Handlers
---------------------------------------------------------------------------
Separated from app.py for SFFB compliance (<200 lines).
Contains the threading bridge and batch processing logic.
Now pushes structured events to the EventBus for LiveConsole rendering.
"""

import asyncio
import logging
import os
import threading
from typing import List, Optional

import httpx

from core.api_client import BOTClient
from core.engine import LedgerEngine
from core.workers.event_bus import EventBus

logger = logging.getLogger(__name__)


class BatchHandler:
    """
    Manages async batch processing in a background thread.
    Bridges the CustomTkinter event loop with asyncio.
    Pushes structured events to the EventBus for the LiveConsole.
    """

    def __init__(self, app, event_bus: Optional[EventBus] = None):
        self.app = app
        self.bus = event_bus or EventBus()

    def start_batch(
        self,
        file_queue: List[str],
        start_date: str,
        dry_run: bool = False,
    ):
        """Launch the batch processing thread."""
        mode = "SIMULATION" if dry_run else "batch"
        self.bus.push({"type": "log", "msg": f"Starting {mode}: {len(file_queue)} ledger(s)..."})
        if dry_run:
            self.bus.push({
                "type": "log",
                "msg": "\u26a0 DRY RUN \u2014 files will NOT be modified.",
            })
        threading.Thread(
            target=self._batch_thread,
            args=(file_queue, start_date, dry_run),
            daemon=True,
        ).start()

    def _batch_thread(
        self,
        file_queue: List[str],
        start_date: str,
        dry_run: bool = False,
    ):
        """Thread target: runs the async batch in a fresh event loop."""
        try:
            asyncio.run(self._run_batch(file_queue, start_date, dry_run))
        except (httpx.ConnectError, httpx.TimeoutException):
            self.bus.push({"type": "error", "msg": "Network error — check your internet connection."})
            try:
                self.app.after(
                    100, self.app._show_error,
                    "Network error — please check your internet connection.",
                )
            except RuntimeError:
                logger.debug("App already destroyed during network error callback")
        except Exception as e:
            self.bus.push({"type": "error", "msg": str(e)})
            try:
                self.app.after(100, self.app._show_error, str(e))
            except RuntimeError:
                logger.debug("App already destroyed during error callback")

    async def _run_batch(
        self,
        file_queue: List[str],
        start_date: str,
        dry_run: bool = False,
    ):
        """Async batch executor."""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api, event_bus=self.bus)

            self.bus.push({"type": "log", "msg": f"API connected. Date: {start_date}"})

            def progress_cb(idx, total, fname, error):
                prefix = "[SIM] " if dry_run else ""
                if error:
                    self.bus.push({
                        "type": "error",
                        "msg": f"{prefix}[{idx}/{total}] {fname} — SKIPPED: {error}",
                    })
                    logger.error("File SKIPPED: %s — %s", fname, error)
                else:
                    self.bus.push({
                        "type": "log",
                        "msg": f"{prefix}[{idx}/{total}] {fname} — OK",
                    })
                try:
                    self.app.after(
                        100, self.app._update_progress,
                        idx, total, fname, error,
                    )
                except RuntimeError:
                    logger.debug("App already destroyed during progress callback")

            success, fail, errors = await engine.process_batch(
                file_queue,
                start_date=start_date,
                progress_cb=progress_cb,
                dry_run=dry_run,
            )
            label = "Simulation" if dry_run else "Batch"
            self.bus.push({
                "type": "success",
                "msg": f"{label} complete: {success} succeeded, {fail} failed.",
            })
            try:
                self.app.after(
                    200, self.app._show_batch_complete, success, fail, errors,
                )
            except RuntimeError:
                logger.debug("App already destroyed during completion callback")

    def start_revert(self, filepath: str):
        """Launch the revert operation in a background thread."""
        self.bus.push({"type": "log", "msg": f"Reverting: {os.path.basename(filepath)}..."})
        threading.Thread(
            target=self._revert_thread,
            args=(filepath,),
            daemon=True,
        ).start()

    def _revert_thread(self, filepath: str):
        """Thread target for the revert operation."""
        from core.backup_manager import BackupError, BackupManager
        try:
            backup_mgr = BackupManager()
            backup_used = backup_mgr.restore_latest(filepath)
            backup_name = os.path.basename(backup_used)
            self.bus.push({"type": "success", "msg": f"Reverted from: {backup_name}"})
            self.app.after(
                0, self.app._show_revert_success, filepath, backup_name,
            )
        except BackupError as e:
            self.bus.push({"type": "error", "msg": f"Revert failed: {e}"})
            self.app.after(0, self.app._show_revert_error, str(e))
        except (OSError, ValueError) as e:
            self.bus.push({"type": "error", "msg": f"Revert failed: {e}"})
            self.app.after(0, self.app._show_revert_error, str(e))
