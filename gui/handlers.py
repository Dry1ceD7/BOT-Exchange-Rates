#!/usr/bin/env python3
"""
gui/handlers.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.5) - Async Processing Handlers
---------------------------------------------------------------------------
Separated from app.py for SFFB compliance (<200 lines).
Contains the threading bridge and batch processing logic.
"""

import asyncio
import logging
import os
import threading
from typing import List

import httpx

from core.api_client import BOTClient
from core.engine import LedgerEngine

logger = logging.getLogger(__name__)


class BatchHandler:
    """
    Manages async batch processing in a background thread.
    Bridges the CustomTkinter event loop with asyncio.
    """

    def __init__(self, app):
        self.app = app

    def start_batch(self, file_queue: List[str], start_date: str):
        """Launch the batch processing thread."""
        threading.Thread(
            target=self._batch_thread,
            args=(file_queue, start_date),
            daemon=True,
        ).start()

    def _batch_thread(self, file_queue: List[str], start_date: str):
        """Thread target: runs the async batch in a fresh event loop."""
        try:
            asyncio.run(self._run_batch(file_queue, start_date))
        except (httpx.ConnectError, httpx.TimeoutException):
            self.app.after(
                0, self.app._show_error,
                "Network error — please check your internet connection.",
            )
        except Exception as e:
            self.app.after(0, self.app._show_error, str(e))

    async def _run_batch(self, file_queue: List[str], start_date: str):
        """Async batch executor."""
        async with httpx.AsyncClient() as client:
            api = BOTClient(client)
            engine = LedgerEngine(api)

            def progress_cb(idx, total, fname, error):
                self.app.after(
                    0, self.app._update_progress,
                    idx, total, fname, error,
                )

            success, fail, errors = await engine.process_batch(
                file_queue, start_date=start_date, progress_cb=progress_cb,
            )
            self.app.after(
                0, self.app._show_batch_complete, success, fail, errors,
            )

    def start_revert(self, filepath: str):
        """Launch the revert operation in a background thread."""
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
            self.app.after(
                0, self.app._show_revert_success, filepath, backup_name,
            )
        except BackupError as e:
            self.app.after(0, self.app._show_revert_error, str(e))
        except Exception as e:
            self.app.after(0, self.app._show_revert_error, str(e))
