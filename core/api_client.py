#!/usr/bin/env python3
"""
core/api_client.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.9) - Featherweight Architecture
---------------------------------------------------------------------------
Handles asynchronous communication with the Bank of Thailand (BOT) API.
Enforces strict JSON schema validation via Pydantic v2.
"""

import asyncio
import os
from datetime import date, timedelta
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type((
            httpx.RequestError, httpx.ConnectError,
            httpx.TimeoutException, httpx.HTTPStatusError,
        ))
    )
    async def _fetch_json(self, url: str, token: str) -> dict:
        # Normalize token: strip any existing "Bearer " prefix to prevent
        # sending "Bearer Bearer <token>" from copy-paste .env errors
        clean_token = token.removeprefix("Bearer ").strip()
        headers = {
            "X-IBM-Client-Id": clean_token,
            "Authorization": f"Bearer {clean_token}",
            "accept": "application/json"
        }
        response = await self.client.get(url, headers=headers, timeout=30.0)
        if response.status_code == 404:
            return {}
        if response.status_code == 429:
            # Rate limited — wait and let tenacity retry
            retry_after = int(response.headers.get("Retry-After", "5"))
            await asyncio.sleep(retry_after)
            response.raise_for_status()  # triggers tenacity retry
        response.raise_for_status()
        return response.json()

    async def get_exchange_rates(self, start_date: date, end_date: date, currency: str) -> List[BOTRateDetail]:
        all_results = []
        current_start = start_date
        while current_start <= end_date:
            current_end = min(current_start + timedelta(days=30), end_date)
            s_str = current_start.strftime("%Y-%m-%d")
            e_str = current_end.strftime("%Y-%m-%d")
            url = f"{self.gateway}{self.exg_path}?start_period={s_str}&end_period={e_str}&currency={currency}"
            raw_json = await self._fetch_json(url, self.token_exg)
            if raw_json and "result" in raw_json:
                try:
                    validated_response = BOTRateResponse.model_validate(raw_json)
                    all_results.extend(validated_response.result.data.data_detail)
                except ValidationError as e:
                    raise BOTAPIError(f"Schema mismatch! {e}")
            current_start = current_end + timedelta(days=1)
            await asyncio.sleep(1.0)  # Rate-limit safe spacing
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
