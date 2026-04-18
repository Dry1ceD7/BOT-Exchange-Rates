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
        """Patch get_token so constructor uses test tokens, not real keychain."""
        _test_tokens = {
            "BOT_TOKEN_EXG": "test_exg_token",
            "BOT_TOKEN_HOL": "test_hol_token",
        }
        monkeypatch.setattr(
            "core.secure_tokens.get_token",
            lambda key: _test_tokens.get(key),
        )

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

    def test_fetch_json_handles_429(self, bot_client, mock_http_client):
        """429 responses are retried with escalating backoff."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock()

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "result": {
                "data": [
                    {"Date": "2025-04-14", "HolidayDescription": "Songkran"},
                ]
            }
        }

        # First call 429, second call success
        mock_http_client.get = AsyncMock(
            side_effect=[rate_limited, success_resp]
        )

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert len(holidays) == 1
        assert holidays[0].description == "Songkran"

    def test_fetch_json_returns_empty_on_404(self, bot_client, mock_http_client):
        """404 responses return empty dict without raising."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert holidays == []

    def test_schema_validation_error_raises(self, bot_client, mock_http_client):
        """Invalid API payload raises BOTAPIError with schema info."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "data": {
                    "data_detail": [
                        {
                            # Missing required "period" field
                            "currency_id": "USD",
                        }
                    ]
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(BOTAPIError, match="Schema mismatch"):
            asyncio.run(bot_client.get_exchange_rates(
                date(2025, 3, 10), date(2025, 3, 10), "USD"
            ))

    def test_multi_chunk_pagination(self, bot_client, mock_http_client):
        """Date ranges > 30 days are split into multiple API chunks."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "data": {
                    "data_detail": [
                        {
                            "period": "2025-01-15",
                            "currency_id": "USD",
                            "buying_transfer": 33.0,
                        }
                    ]
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        # 62-day range → should produce 2 API calls (Jan1-Jan31, Feb1-Mar3)
        rates = asyncio.run(bot_client.get_exchange_rates(
            date(2025, 1, 1), date(2025, 3, 3), "USD"
        ))

        assert mock_http_client.get.call_count == 2
        assert len(rates) == 2  # 1 result per chunk * 2 chunks
