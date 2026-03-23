#!/usr/bin/env python3
"""
core/engine_factory.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.0) — Strategy Pattern Engine Factory
---------------------------------------------------------------------------
OS-Level Router: detects sys.platform at runtime and returns the correct
engine implementation. Both engines share the BaseEngine ABC interface,
guaranteeing Liskov Substitution across Windows/macOS/Linux.

Usage:
    from core.engine_factory import get_engine
    engine = get_engine(api_client)
    await engine.process_ledger(filepath)
"""

import sys
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

from core.api_client import BOTClient


class BaseEngine(ABC):
    """Abstract contract that all platform engines must implement."""

    def __init__(self, api_client: BOTClient):
        self.api = api_client

    @abstractmethod
    async def process_ledger(
        self,
        filepath: str,
        start_date: Optional[str] = None,
        excel=None,
    ) -> str:
        """Process a single ledger file. Returns the output filepath."""
        ...

    @abstractmethod
    async def process_batch(
        self,
        filepaths: List[str],
        start_date: Optional[str] = None,
        progress_cb: Optional[
            Callable[[int, int, str, Optional[str]], None]
        ] = None,
    ) -> Tuple[int, int, List[str]]:
        """Batch-process ledger files. Returns (success, failed, errors)."""
        ...


class FallbackExcelEngine(BaseEngine):
    """
    Pure openpyxl engine for macOS and Linux.

    Reads and writes .xlsx files entirely in memory without requiring
    a native Excel installation. Mirrors the NativeExcelEngine API
    footprint so the rest of the application is platform-blind.
    """

    def __init__(self, api_client: BOTClient):
        super().__init__(api_client)
        # Delegate to the existing LedgerEngine which already has
        # the full openpyxl path implemented.
        from core.engine import LedgerEngine
        self._delegate = LedgerEngine(api_client)

    async def process_ledger(
        self,
        filepath: str,
        start_date: Optional[str] = None,
        excel=None,
    ) -> str:
        return await self._delegate.process_ledger(
            filepath, start_date=start_date, excel=excel,
        )

    async def process_batch(
        self,
        filepaths: List[str],
        start_date: Optional[str] = None,
        progress_cb: Optional[
            Callable[[int, int, str, Optional[str]], None]
        ] = None,
    ) -> Tuple[int, int, List[str]]:
        return await self._delegate.process_batch(
            filepaths, start_date=start_date, progress_cb=progress_cb,
        )


class NativeExcelEngine(BaseEngine):
    """
    Windows-only COM engine powered by win32com.client.

    Spawns an invisible Excel.Application instance for maximum
    formatting fidelity and performance on Windows hardware.
    MUST NOT be imported on non-Windows platforms.
    """

    def __init__(self, api_client: BOTClient):
        super().__init__(api_client)
        from core.engine import LedgerEngine
        self._delegate = LedgerEngine(api_client)

    async def process_ledger(
        self,
        filepath: str,
        start_date: Optional[str] = None,
        excel=None,
    ) -> str:
        return await self._delegate.process_ledger(
            filepath, start_date=start_date, excel=excel,
        )

    async def process_batch(
        self,
        filepaths: List[str],
        start_date: Optional[str] = None,
        progress_cb: Optional[
            Callable[[int, int, str, Optional[str]], None]
        ] = None,
    ) -> Tuple[int, int, List[str]]:
        return await self._delegate.process_batch(
            filepaths, start_date=start_date, progress_cb=progress_cb,
        )


def get_engine_class() -> type:
    """Return the appropriate engine class for the current OS."""
    if sys.platform == "win32":
        return NativeExcelEngine
    return FallbackExcelEngine


def get_engine(api_client: BOTClient) -> BaseEngine:
    """Factory: instantiate the correct engine for the current OS."""
    cls = get_engine_class()
    return cls(api_client)
