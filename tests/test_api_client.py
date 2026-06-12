#!/usr/bin/env python3
"""
tests/test_api_client.py
---------------------------------------------------------------------------
Unit tests for core/api_client.py — BOTClient with mocked HTTP responses.
---------------------------------------------------------------------------
"""

import asyncio
import json as jsonlib
import logging
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from core.api_client import (
    BOTAPIError,
    BOTClient,
    BOTHolidayDetail,
    BOTRateDetail,
    BOTTransientServerError,
    TokenRedactionFilter,
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
        assert rate.selling == Decimal("33.10")
        assert rate.buying_transfer == Decimal("32.60")

    def test_optional_fields_default_to_none(self):
        data = {
            "period": "2025-03-10",
            "currency_id": "EUR",
        }
        rate = BOTRateDetail(**data)
        assert rate.selling is None
        assert rate.buying_transfer is None

    def test_rate_fields_are_exact_4dp_decimals(self):
        """Layer-1 exactness gate: every rate field is a Decimal quantized
        to 4dp at the parse boundary — never a binary float."""
        rate = BOTRateDetail(
            period="2025-03-10", currency_id="USD",
            buying_transfer=32.60, buying_sight="32.55",
            selling=Decimal("33.1"), mid_rate=32.875,
        )
        for field in ("buying_transfer", "buying_sight", "selling", "mid_rate"):
            value = getattr(rate, field)
            assert isinstance(value, Decimal)
            assert value.as_tuple().exponent == -4
        # str-safe construction preserves the human-readable digits.
        assert str(rate.buying_transfer) == "32.6000"
        assert str(rate.buying_sight) == "32.5500"
        assert str(rate.selling) == "33.1000"
        assert str(rate.mid_rate) == "32.8750"

    def test_six_dp_value_quantizes_half_even_to_4dp(self):
        rate = BOTRateDetail(
            period="2025-03-10", currency_id="USD",
            buying_transfer=34.123456, selling=Decimal("34.12345"),
        )
        assert rate.buying_transfer == Decimal("34.1235")
        # Exact tie rounds half-even (banker's rounding), matching
        # safe_to_decimal's Decimal-default rounding discipline.
        assert rate.selling == Decimal("34.1234")

    def test_empty_string_rate_becomes_none(self):
        rate = BOTRateDetail(
            period="2025-03-10", currency_id="USD", buying_transfer="",
        )
        assert rate.buying_transfer is None

    def test_non_numeric_rate_raises_validation_error(self):
        """Junk must still FAIL schema validation (surfaced upstream as the
        generic BOTAPIError), never be silently coerced or dropped."""
        with pytest.raises(ValidationError):
            BOTRateDetail(
                period="2025-03-10", currency_id="USD",
                buying_transfer="not-a-number",
            )


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
        assert isinstance(rates[0].selling, Decimal)
        assert rates[0].selling == Decimal("33.10")

    def test_rates_parse_decimal_from_literal_json_token(
        self, bot_client, mock_http_client,
    ):
        """Layer-1 exactness gate: _fetch_json must parse JSON numbers into
        Decimal straight from the literal response token (parse_float=Decimal),
        never via an intermediate binary float.

        Discriminator: the literal token below quantizes to 34.1235 when
        parsed exactly, but to 34.1234 (half-even on the float's shortest
        repr "34.12345") if it ever passes through a float.
        """
        # Raw body string: the discriminator token must reach json.loads
        # verbatim (building it via dumps() would collapse it to a float).
        body = (
            '{"result": {"data": {"data_detail": [{'
            '"period": "2025-03-10", "currency_id": "USD", '
            '"buying_transfer": 34.12345000000000001, "selling": 33.10'
            '}]}}}'
        )
        # A response whose .json() honors kwargs like the real httpx Response.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda **kw: jsonlib.loads(body, **kw)
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        rates = asyncio.run(bot_client.get_exchange_rates(
            date(2025, 3, 10), date(2025, 3, 10), "USD"
        ))
        assert rates[0].buying_transfer == Decimal("34.1235")
        assert str(rates[0].selling) == "33.1000"

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

        # SECURITY: generic message — raw ValidationError (response body)
        # must NOT be surfaced to the user.
        with pytest.raises(BOTAPIError, match="unexpected schema"):
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

    def test_holidays_schema_error_is_generic(self, bot_client, mock_http_client):
        """SECURITY: holiday schema errors must not leak the raw response body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "data": [
                    {
                        # Missing required "Date" field → ValidationError
                        "HolidayDescription": "Songkran",
                    }
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(BOTAPIError) as exc_info:
            asyncio.run(bot_client.get_holidays(2025))
        # Generic message only — no raw ValidationError detail leaked.
        assert str(exc_info.value) == "BOT holiday API returned an unexpected schema."
        assert "validation error" not in str(exc_info.value).lower()

    def test_non_numeric_retry_after_does_not_crash(
        self, bot_client, mock_http_client, monkeypatch,
    ):
        """RFC-7231 HTTP-date Retry-After values fall back to 5s, no crash."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        # An HTTP-date, not delta-seconds — int() would otherwise raise.
        rate_limited.headers = {"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"}
        rate_limited.raise_for_status = MagicMock()

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "result": {"data": [{"Date": "2025-04-14", "HolidayDescription": "Songkran"}]}
        }

        sleeps: list = []

        async def _fake_sleep(secs):
            sleeps.append(secs)

        monkeypatch.setattr("core.api_client.asyncio.sleep", _fake_sleep)
        mock_http_client.get = AsyncMock(side_effect=[rate_limited, success_resp])

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert len(holidays) == 1
        # Fell back to 5s (attempt 0 → 5 + 0).
        assert sleeps == [5]

    def test_retry_after_clamped_to_max(self):
        """A huge numeric Retry-After is clamped to RETRY_AFTER_MAX_SECONDS."""
        from core.constants import RETRY_AFTER_MAX_SECONDS
        assert BOTClient._parse_retry_after("999999") == RETRY_AFTER_MAX_SECONDS
        assert BOTClient._parse_retry_after("-3") == 5
        assert BOTClient._parse_retry_after(None) == 5
        assert BOTClient._parse_retry_after("12") == 12

    def test_5xx_is_retried_then_succeeds(self, bot_client, mock_http_client, monkeypatch):
        """Transient 5xx triggers a tenacity retry that then succeeds."""
        server_error = MagicMock()
        server_error.status_code = 503
        server_error.raise_for_status = MagicMock()

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "result": {"data": [{"Date": "2025-04-14", "HolidayDescription": "Songkran"}]}
        }

        # Make tenacity's async backoff instant so the test is fast.
        async def _instant_sleep(secs):
            return None

        monkeypatch.setattr(
            BOTClient._fetch_json.retry, "sleep", _instant_sleep,
        )
        mock_http_client.get = AsyncMock(side_effect=[server_error, success_resp])

        holidays = asyncio.run(bot_client.get_holidays(2025))
        assert holidays[0].description == "Songkran"
        assert mock_http_client.get.call_count == 2

    def test_transient_server_error_is_distinct_from_api_error(self):
        """The retryable 5xx type must not be a BOTAPIError (which fails fast)."""
        assert issubclass(BOTTransientServerError, Exception)
        assert not issubclass(BOTTransientServerError, BOTAPIError)

    def test_4xx_fails_fast_without_retry(self, bot_client, mock_http_client):
        """A non-auth 4xx client error fails fast (no transient-retry)."""
        client_error = MagicMock()
        client_error.status_code = 400
        client_error.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "bad request", request=MagicMock(), response=MagicMock(),
            )
        )
        mock_http_client.get = AsyncMock(return_value=client_error)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(bot_client.get_holidays(2025))
        # No retry — single attempt.
        assert mock_http_client.get.call_count == 1

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failure_raises_actionable_message(
        self, status, bot_client, mock_http_client,
    ):
        """401/403 → a clear, actionable BOTAPIError (not a raw httpx URL).

        Regression for the audit finding: a non-technical accountant must be
        told their key was rejected and where to re-enter it, never shown the
        raw status URL / MDN link from raise_for_status().
        """
        auth_error = MagicMock()
        auth_error.status_code = status
        # If the code path ever fell through to raise_for_status() this would
        # surface the raw httpx message — assert it does NOT get called.
        auth_error.raise_for_status = MagicMock(
            side_effect=AssertionError("raise_for_status must not run for auth errors")
        )
        mock_http_client.get = AsyncMock(return_value=auth_error)

        with pytest.raises(BOTAPIError) as exc_info:
            asyncio.run(bot_client.get_holidays(2025))

        msg = str(exc_info.value)
        assert str(status) in msg
        assert "re-enter your keys" in msg.lower()
        # SECURITY: the message must not leak the request URL or any token.
        assert "http" not in msg.lower()
        assert "gateway.api.bot.or.th" not in msg
        assert bot_client.token_hol not in msg
        # Auth failure fails fast — no retry.
        assert mock_http_client.get.call_count == 1

    def test_auth_failure_propagates_through_get_exchange_rates(
        self, bot_client, mock_http_client,
    ):
        """The actionable auth message reaches the exchange-rate caller too."""
        auth_error = MagicMock()
        auth_error.status_code = 401
        auth_error.raise_for_status = MagicMock()
        mock_http_client.get = AsyncMock(return_value=auth_error)

        with pytest.raises(BOTAPIError, match="rejected"):
            asyncio.run(bot_client.get_exchange_rates(
                date(2025, 3, 10), date(2025, 3, 10), "USD",
            ))


# =========================================================================
#  ping_token() — first-run credential probe
# =========================================================================

class TestPingToken:
    """ping_token() distinguishes accepted / rejected / unreachable keys."""

    def test_empty_token_short_circuits(self):
        from core.api_client import ping_token

        ok, msg = ping_token("")
        assert ok is False
        assert "enter a key" in msg.lower()

    def test_accepted_key_returns_ok(self, monkeypatch):
        from core import api_client

        resp = MagicMock()
        resp.status_code = 200
        monkeypatch.setattr(api_client.httpx, "get", lambda *a, **k: resp)

        ok, msg = api_client.ping_token("VALIDKEY123")
        assert ok is True
        assert "verified" in msg.lower()

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_key_returns_not_ok(self, status, monkeypatch):
        from core import api_client

        resp = MagicMock()
        resp.status_code = status
        monkeypatch.setattr(api_client.httpx, "get", lambda *a, **k: resp)

        ok, msg = api_client.ping_token("BADKEY12345")
        assert ok is False
        assert "rejected" in msg.lower()

    def test_other_status_reports_http_code(self, monkeypatch):
        from core import api_client

        resp = MagicMock()
        resp.status_code = 500
        monkeypatch.setattr(api_client.httpx, "get", lambda *a, **k: resp)

        ok, msg = api_client.ping_token("SOMEKEY1234")
        assert ok is False
        assert "500" in msg

    def test_network_failure_is_friendly_and_tokenless(self, monkeypatch):
        from core import api_client

        def _boom(*a, **k):
            raise httpx.ConnectError("getaddrinfo failed for SECRETTOKEN999")

        monkeypatch.setattr(api_client.httpx, "get", _boom)

        ok, msg = api_client.ping_token("SECRETTOKEN999")
        assert ok is False
        assert "could not reach" in msg.lower()
        # SECURITY: never echo the token or the raw exception text.
        assert "SECRETTOKEN999" not in msg
        assert "getaddrinfo" not in msg

    def test_token_is_bearer_stripped_in_headers(self, monkeypatch):
        from core import api_client

        captured = {}

        def _capture(url, *, headers, timeout):
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            return resp

        monkeypatch.setattr(api_client.httpx, "get", _capture)
        api_client.ping_token("Bearer  RAWKEY123 ")
        assert captured["headers"]["X-IBM-Client-Id"] == "RAWKEY123"
        assert captured["headers"]["Authorization"] == "Bearer RAWKEY123"

    def test_default_product_probes_exchange_endpoint(self, monkeypatch):
        """Backward-compatible default: the EXG-rate product is probed."""
        from core import api_client

        captured = {}

        def _capture(url, *, headers, timeout):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            return resp

        monkeypatch.setattr(api_client.httpx, "get", _capture)
        api_client.ping_token("SOMEKEY1234")
        assert api_client.EXG_RATE_PATH in captured["url"]

    def test_hol_product_probes_holiday_endpoint(self, monkeypatch):
        """product='hol' must hit the holiday endpoint the batch depends on.

        The BOT gateway scopes each key to one API product (live-verified:
        a valid HOL key 403s on the EXG endpoint and vice versa), so probing
        the holiday key against the exchange-rate endpoint rejects correct
        keys and passes wrong ones.
        """
        from core import api_client

        captured = {}

        def _capture(url, *, headers, timeout):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            return resp

        monkeypatch.setattr(api_client.httpx, "get", _capture)
        ok, _ = api_client.ping_token("HOLKEY12345", product="hol")
        assert ok is True
        assert api_client.HOLIDAY_PATH in captured["url"]
        assert api_client.EXG_RATE_PATH not in captured["url"]


# =========================================================================
#  TOKEN REDACTION LOGGING FILTER (security)
# =========================================================================

class TestTokenRedactionFilter:
    """The filter must scrub token values out of log records."""

    def _make_record(self, msg, args=()):
        return logging.LogRecord(
            name="test", level=logging.WARNING, pathname=__file__,
            lineno=1, msg=msg, args=args, exc_info=None,
        )

    def test_scrubs_token_from_message(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN_EXG", "supersecrettoken123")
        f = TokenRedactionFilter()
        rec = self._make_record("requesting with token supersecrettoken123 done")
        assert f.filter(rec) is True
        assert "supersecrettoken123" not in rec.getMessage()
        assert "***" in rec.getMessage()

    def test_scrubs_token_in_args(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN_HOL", "holtoken999")
        f = TokenRedactionFilter()
        rec = self._make_record("header=%s", ("holtoken999",))
        assert f.filter(rec) is True
        assert "holtoken999" not in rec.getMessage()

    def test_passes_through_when_no_token(self, monkeypatch):
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        f = TokenRedactionFilter()
        rec = self._make_record("nothing secret here")
        assert f.filter(rec) is True
        assert rec.getMessage() == "nothing secret here"

    def test_scrubs_keychain_only_token(self, monkeypatch):
        """SECURITY: token living ONLY in the keychain (env scrubbed) is redacted."""
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)

        # Simulate post-migration state: os.environ empty, value in keychain.
        def _fake_get_token(env_key):
            return "keychainOnlySecret" if env_key == "BOT_TOKEN_EXG" else None

        monkeypatch.setattr("core.secure_tokens.get_token", _fake_get_token)
        f = TokenRedactionFilter()
        rec = self._make_record("leaked keychainOnlySecret in url")
        assert f.filter(rec) is True
        assert "keychainOnlySecret" not in rec.getMessage()
        assert "***" in rec.getMessage()

    def test_scrubs_registered_token(self, monkeypatch):
        """SECURITY: a token registered by a live client is redacted."""
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        monkeypatch.setattr("core.secure_tokens.get_token", lambda key: None)
        f = TokenRedactionFilter()
        f.register_tokens("liveClientToken")
        rec = self._make_record("connecting with liveClientToken now")
        assert f.filter(rec) is True
        assert "liveClientToken" not in rec.getMessage()

    def test_registered_tokens_skip_keychain_probe(self, monkeypatch):
        """PERF: once a client registers tokens, the keychain is never probed.

        get_token() can fire several real keychain syscalls (and unlock
        prompts) per call; the hot path must not touch it once we already know
        the live token values.
        """
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        calls = {"n": 0}

        def _spy_get_token(env_key):
            calls["n"] += 1
            return None

        monkeypatch.setattr("core.secure_tokens.get_token", _spy_get_token)
        f = TokenRedactionFilter()
        f.register_tokens("liveClientToken")
        for _ in range(5):
            f.filter(self._make_record("noise liveClientToken noise"))
        assert calls["n"] == 0

    def test_keychain_probe_is_memoized(self, monkeypatch):
        """PERF: without registered tokens the env/keychain probe is cached,
        not re-queried on every log record (featherweight hot-path guard)."""
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        calls = {"n": 0}

        def _spy_get_token(env_key):
            calls["n"] += 1
            return "keychainSecret" if env_key == "BOT_TOKEN_EXG" else None

        monkeypatch.setattr("core.secure_tokens.get_token", _spy_get_token)
        f = TokenRedactionFilter()
        for _ in range(5):
            rec = self._make_record("leaked keychainSecret here")
            assert f.filter(rec) is True
            assert "keychainSecret" not in rec.getMessage()
        # Two env keys probed exactly once across all five records (cached),
        # not 2x per record.
        assert calls["n"] == 2

    def _make_exc_record(self, msg="boom"):
        """Build a record carrying a live exc_info whose message embeds a token."""
        try:
            raise ValueError(
                "connect failed: https://gateway/?client_id=tbsecret456"
            )
        except ValueError:
            exc_info = sys.exc_info()
        return logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=exc_info,
        )

    def test_redacts_token_in_exception_traceback(self, monkeypatch):
        """SECURITY (F24): a token embedded in the traceback is scrubbed."""
        monkeypatch.setenv("BOT_TOKEN_EXG", "tbsecret456")
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        f = TokenRedactionFilter()
        rec = self._make_exc_record()
        assert f.filter(rec) is True
        formatted = logging.Formatter().format(rec)
        assert "tbsecret456" not in formatted
        assert "***" in formatted

    def test_traceback_formatting_preserved_after_redaction(self, monkeypatch):
        """The redacted record still formats as a full traceback block."""
        monkeypatch.setenv("BOT_TOKEN_EXG", "tbsecret456")
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        f = TokenRedactionFilter()
        rec = self._make_exc_record()
        assert f.filter(rec) is True
        formatted = logging.Formatter().format(rec)
        assert "Traceback (most recent call last)" in formatted
        assert "ValueError" in formatted
        # exc_info cleared so no handler can re-format the raw exception;
        # the pre-redacted exc_text drives the output instead.
        assert rec.exc_info is None
        assert rec.exc_text is not None
        assert not rec.exc_text.endswith("\n")

    def test_exc_info_untouched_when_no_tokens(self, monkeypatch):
        """Without known tokens the record's exc_info passes through as-is."""
        monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
        monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
        monkeypatch.setattr("core.secure_tokens.get_token", lambda key: None)
        f = TokenRedactionFilter()
        rec = self._make_exc_record()
        assert f.filter(rec) is True
        assert rec.exc_info is not None


# =========================================================================
#  CONSTRUCTOR WIRING (timeout from settings + token registration)
# =========================================================================

class TestBOTClientConstruction:
    """Constructor reads timeout from settings and registers tokens."""

    @pytest.fixture(autouse=True)
    def _patch_tokens(self, monkeypatch):
        monkeypatch.setattr(
            "core.secure_tokens.get_token",
            lambda key: {"BOT_TOKEN_EXG": "exg", "BOT_TOKEN_HOL": "hol"}.get(key),
        )

    def test_timeout_from_settings(self, monkeypatch):
        from core import api_client
        monkeypatch.setattr(
            "core.config_manager.SettingsManager.get",
            lambda self, key, default=None: 7 if key == "api_timeout_seconds" else default,
        )
        client = api_client.BOTClient(AsyncMock())
        assert client.timeout_seconds == 7.0

    def test_timeout_falls_back_to_constant(self, monkeypatch):
        from core import api_client
        from core.constants import API_TIMEOUT_SECONDS

        def _boom(self, key, default=None):
            raise RuntimeError("settings unreadable")

        monkeypatch.setattr(
            "core.config_manager.SettingsManager.get", _boom,
        )
        client = api_client.BOTClient(AsyncMock())
        assert client.timeout_seconds == API_TIMEOUT_SECONDS

    @pytest.mark.parametrize("bad", [0.001, 1e12])
    def test_out_of_range_timeout_falls_back_to_constant(
        self, monkeypatch, bad,
    ):
        """Round 11: hand-edited settings.json bypasses the import-side
        coercion (only import_settings calls _coerce_imported), so the
        client must reject a pathological timeout itself — 0.001 makes
        every BOT call time out (x4 tenacity retries per chunk), 1e12
        hangs ~forever on a dead network."""
        from core import api_client
        from core.constants import API_TIMEOUT_SECONDS
        monkeypatch.setattr(
            "core.config_manager.SettingsManager.get",
            lambda self, key, default=None: (
                bad if key == "api_timeout_seconds" else default
            ),
        )
        client = api_client.BOTClient(AsyncMock())
        assert client.timeout_seconds == API_TIMEOUT_SECONDS

    def test_constructor_registers_tokens_with_filter(self, monkeypatch):
        from core import api_client
        registered: list = []
        monkeypatch.setattr(
            api_client, "register_redaction_tokens",
            lambda *toks: registered.extend(toks),
        )
        api_client.BOTClient(AsyncMock())
        assert "exg" in registered
        assert "hol" in registered


# =========================================================================
#  INTER-CHUNK COOLDOWN (Round 11): sleep BETWEEN chunks only
# =========================================================================

class TestInterChunkCooldown:
    """The 0.3-0.8s jitter cooldown must run between chunks only — the old
    loop also slept after the LAST chunk (pure dead time: ~3.3s on the
    default Dec-20->today window, ~6.6s per currency on a full year)."""

    @pytest.fixture(autouse=True)
    def _patch_tokens(self, monkeypatch):
        monkeypatch.setattr(
            "core.secure_tokens.get_token",
            lambda key: {"BOT_TOKEN_EXG": "exg", "BOT_TOKEN_HOL": "hol"}.get(key),
        )

    @pytest.mark.parametrize(
        ("span_days", "expected_chunks"),
        [
            (0, 1),    # single-chunk window -> zero sleeps
            (30, 1),   # exactly one 31-day chunk -> zero sleeps
            (31, 2),   # two chunks -> one sleep
            (70, 3),   # three chunks -> two sleeps
        ],
    )
    def test_sleep_count_is_chunks_minus_one(
        self, monkeypatch, span_days, expected_chunks,
    ):
        from datetime import timedelta

        from core import api_client

        sleeps: list[float] = []

        async def _fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("core.api_client.asyncio.sleep", _fake_sleep)

        client = api_client.BOTClient(AsyncMock())
        # Bypass the tenacity-wrapped fetch entirely: {} means "no data".
        client._fetch_json = AsyncMock(return_value={})

        start = date(2025, 1, 1)
        result = asyncio.run(
            client.get_exchange_rates(
                start, start + timedelta(days=span_days), "USD",
            )
        )
        assert result == []
        assert client._fetch_json.await_count == expected_chunks
        assert len(sleeps) == expected_chunks - 1
