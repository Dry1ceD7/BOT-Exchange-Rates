#!/usr/bin/env python3
"""
core/api_client.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Featherweight Architecture
---------------------------------------------------------------------------
Handles asynchronous communication with the Bank of Thailand (BOT) API.
Enforces strict JSON schema validation via Pydantic v2.
"""

import asyncio
import logging
import os
import random
from datetime import date, timedelta
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# PYDANTIC v2 SCHEMAS
# -------------------------------------------------------------------------

class BOTRateDetail(BaseModel):
    """Schema for a single day's exchange rate data point."""
    period: str
    currency: str = Field(alias="currency_id")
    buying_transfer: Optional[float] = None
    selling: Optional[float] = None

class BOTRateData(BaseModel):
    data_detail: List[BOTRateDetail]

class BOTRateResult(BaseModel):
    data: BOTRateData

class BOTRateResponse(BaseModel):
    """Master schema for the BOT Exchange Rate API payload."""
    result: BOTRateResult

class BOTHolidayDetail(BaseModel):
    """Schema for a single BOT public holiday."""
    date: str = Field(alias="Date")
    description: str = Field(alias="HolidayDescription")

class BOTHolidayResult(BaseModel):
    data: List[BOTHolidayDetail]

class BOTHolidayResponse(BaseModel):
    """Master schema for the BOT Holiday API payload."""
    result: BOTHolidayResult

# -------------------------------------------------------------------------
# EXCEPTION HANDLING
# -------------------------------------------------------------------------

class BOTAPIError(Exception):
    """Custom exception raised when the BOT API fails."""
    pass

# -------------------------------------------------------------------------
# ASYNC API CLIENT
# -------------------------------------------------------------------------

# Recommended timeout for httpx.AsyncClient constructor
CLIENT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class BOTClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.token_exg = os.environ.get("BOT_TOKEN_EXG")
        self.token_hol = os.environ.get("BOT_TOKEN_HOL")

        if not self.token_exg or not self.token_hol:
            raise BOTAPIError("Missing BOT API tokens.")

        self.gateway = "https://gateway.api.bot.or.th"
        self.exg_path = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
        self.hol_path = "/financial-institutions-holidays/"

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((
            httpx.RequestError, httpx.ConnectError,
            httpx.TimeoutException,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _fetch_json(self, url: str, token: str) -> dict:
        """Fetch JSON from BOT API with built-in 429 rate limit handling.

        429 responses are handled internally (infinite loop with backoff)
        and do NOT consume tenacity retry attempts. Tenacity only retries
        on real connection/timeout errors.
        """
        clean_token = token.removeprefix("Bearer ").strip()
        headers = {
            "X-IBM-Client-Id": clean_token,
            "Authorization": f"Bearer {clean_token}",
            "accept": "application/json"
        }

        max_429_retries = 10  # safety: max 10 rate-limit waits per request
        for attempt_429 in range(max_429_retries):
            response = await self.client.get(url, headers=headers, timeout=30.0)

            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {}
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                wait_time = retry_after + attempt_429  # escalating wait
                logger.warning(
                    "429 Rate limited (attempt %d/%d). Waiting %ds...",
                    attempt_429 + 1, max_429_retries, wait_time,
                )
                await asyncio.sleep(wait_time)
                continue

            # Any other error → raise for tenacity or caller
            response.raise_for_status()

        raise BOTAPIError(
            f"BOT API rate limit exceeded after {max_429_retries} waits. "
            "Please try again in a few minutes."
        )

    async def get_exchange_rates(
        self, start_date: date, end_date: date, currency: str,
    ) -> List[BOTRateDetail]:
        all_results = []
        current_start = start_date

        # Count total chunks for progress logging
        total_chunks = 0
        tmp = start_date
        while tmp <= end_date:
            total_chunks += 1
            tmp += timedelta(days=31)

        chunk_idx = 0
        while current_start <= end_date:
            current_end = min(current_start + timedelta(days=30), end_date)
            s_str = current_start.strftime("%Y-%m-%d")
            e_str = current_end.strftime("%Y-%m-%d")
            chunk_idx += 1
            logger.info(
                "API [%s] chunk %d/%d: %s → %s",
                currency, chunk_idx, total_chunks, s_str, e_str,
            )
            url = (
                f"{self.gateway}{self.exg_path}"
                f"?start_period={s_str}&end_period={e_str}&currency={currency}"
            )
            raw_json = await self._fetch_json(url, self.token_exg)
            if raw_json and "result" in raw_json:
                try:
                    validated_response = BOTRateResponse.model_validate(raw_json)
                    all_results.extend(validated_response.result.data.data_detail)
                except ValidationError as e:
                    raise BOTAPIError(f"Schema mismatch! {e}")
            current_start = current_end + timedelta(days=1)
            # Inter-chunk cooldown: 1-3s prevents rate limiting
            await asyncio.sleep(random.uniform(1.0, 3.0))
        return all_results

    async def get_holidays(self, year: int) -> List[BOTHolidayDetail]:
        url = f"{self.gateway}{self.hol_path}?year={year}"
        raw_json = await self._fetch_json(url, self.token_hol)
        if not raw_json or "result" not in raw_json:
            return []
        try:
            validated_response = BOTHolidayResponse.model_validate(raw_json)
            return validated_response.result.data
        except ValidationError as e:
            raise BOTAPIError(f"Schema mismatch! {e}")
