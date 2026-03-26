#!/usr/bin/env python3
"""
core/engine_factory.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Strategy Pattern Engine Factory
---------------------------------------------------------------------------
Returns the correct engine implementation. Both platforms now use the
same openpyxl-based LedgerEngine since COM/.xls support was removed.

Usage:
    from core.engine_factory import get_engine
    engine = get_engine(api_client)
    await engine.process_ledger(filepath)
"""

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


class OpenpyxlEngine(BaseEngine):
    """
    Pure openpyxl engine for all platforms.

    Reads and writes .xlsx files entirely in memory without requiring
    a native Excel installation.
    """

    def __init__(self, api_client: BOTClient):
        super().__init__(api_client)
        from core.engine import LedgerEngine
        self._delegate = LedgerEngine(api_client)

    async def process_ledger(
        self,
        filepath: str,
        start_date: Optional[str] = None,
    ) -> str:
        return await self._delegate.process_ledger(
            filepath, start_date=start_date,
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
    """Return the engine class (always OpenpyxlEngine now)."""
    return OpenpyxlEngine


def get_engine(api_client: BOTClient) -> BaseEngine:
    """Factory: instantiate the engine."""
    cls = get_engine_class()
    return cls(api_client)
