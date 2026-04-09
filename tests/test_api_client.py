#!/usr/bin/env python3
"""
tests/test_api_client.py
---------------------------------------------------------------------------
Unit tests for core/api_client.py — BOTClient with mocked HTTP responses.
---------------------------------------------------------------------------
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.api_client import (
    BOTAPIError,
    BOTClient,
    BOTHolidayDetail,
    BOTRateDetail,
)

# =========================================================================
#  PYDANTIC SCHEMA TESTS
# =========================================================================

class TestBOTRateDetail:
    """Tests for Pydantic rate schema validation."""

    def test_valid_rate_detail(self):
        data = {
            "period": "2025-03-10",
            "currency_id": "USD",
            "buying_transfer": 32.60,
            "selling": 33.10,
        }
        rate = BOTRateDetail(**data)
        assert rate.period == "2025-03-10"
        assert rate.currency == "USD"
        assert rate.selling == 33.10

    def test_optional_fields_default_to_none(self):
        data = {
            "period": "2025-03-10",
            "currency_id": "EUR",
        }
        rate = BOTRateDetail(**data)
        assert rate.selling is None
        assert rate.buying_transfer is None


class TestBOTHolidayDetail:
    """Tests for Pydantic holiday schema validation."""

    def test_valid_holiday(self):
        data = {
            "Date": "2025-01-01",
            "HolidayDescription": "New Year's Day",
        }
        h = BOTHolidayDetail(**data)
        assert h.date == "2025-01-01"
        assert h.description == "New Year's Day"


# =========================================================================
#  BOTClient TESTS (mocked HTTP)
# =========================================================================

class TestBOTClient:
    """Tests for BOTClient API calls with mocked responses."""

    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        """Set BOT tokens so constructor doesn't raise."""
        monkeypatch.setenv("BOT_TOKEN_EXG", "test_exg_token")
        monkeypatch.setenv("BOT_TOKEN_HOL", "test_hol_token")

    @pytest.fixture
    def mock_http_client(self):
        return AsyncMock()

    @pytest.fixture
    def bot_client(self, mock_http_client):
        return BOTClient(mock_http_client)

    def test_constructor_sets_tokens(self, bot_client):
        assert bot_client.token_exg == "test_exg_token"
        assert bot_client.token_hol == "test_hol_token"

    def test_constructor_raises_without_tokens(self, monkeypatch):
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        monkeypatch.setattr("core.secure_tokens.get_token", lambda x: None)
        with pytest.raises(BOTAPIError, match="Missing BOT API tokens"):
            BOTClient(AsyncMock())

    def test_get_exchange_rates_parses_response(self, bot_client, mock_http_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "data": {
                    "data_detail": [
                        {
                            "period": "2025-03-10",
                            "currency_id": "USD",
                            "buying_transfer": 32.60,
                            "selling": 33.10,
                        }
                    ]
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        rates = asyncio.run(bot_client.get_exchange_rates(
            date(2025, 3, 10), date(2025, 3, 10), "USD"
        ))
        assert len(rates) == 1
        assert rates[0].period == "2025-03-10"
        assert rates[0].selling == 33.10

    def test_get_holidays_parses_response(self, bot_client, mock_http_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "data": [
                    {
                        "Date": "2025-01-01",
                        "HolidayDescription": "New Year's Day",
                    }
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert len(holidays) == 1
        assert holidays[0].description == "New Year's Day"

    def test_get_holidays_empty_response(self, bot_client, mock_http_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert holidays == []
