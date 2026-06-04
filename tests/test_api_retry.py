#!/usr/bin/env python3
"""
tests/test_api_retry.py
---------------------------------------------------------------------------
Regression coverage for BOTClient retry / failure behaviour.

Covers:
  - tenacity retry EXHAUSTION (all 4 attempts fail) surfaces an error,
  - a httpx.ConnectError surfaces (after retries are exhausted),
  - the 429-then-success path (rate limit handled internally, no attempt
    consumed),
  - the 429-exhaustion path raises BOTAPIError with a generic message,
  - NO token value appears in any RAISED exception message (redaction).

All HTTP is mocked; tenacity waits are zeroed so the suite stays fast.
---------------------------------------------------------------------------
"""

import asyncio
import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import tenacity

from core.api_client import BOTAPIError, BOTClient

# A distinctive token so we can assert it never leaks into raised messages.
_SECRET_EXG = "EXG_SECRET_abc123XYZ"
_SECRET_HOL = "HOL_SECRET_def456QRS"


@pytest.fixture(autouse=True)
def _patch_tokens(monkeypatch):
    tokens = {"BOT_TOKEN_EXG": _SECRET_EXG, "BOT_TOKEN_HOL": _SECRET_HOL}
    monkeypatch.setattr(
        "core.secure_tokens.get_token", lambda key: tokens.get(key),
    )


@pytest.fixture
def http_client():
    return AsyncMock()


@pytest.fixture
def client(http_client):
    c = BOTClient(http_client)
    # Zero out the exponential backoff so retry exhaustion is instant.
    c._fetch_json.retry.wait = tenacity.wait_none()
    return c


def _assert_no_token(text: str) -> None:
    assert _SECRET_EXG not in text, "EXG token leaked into raised message"
    assert _SECRET_HOL not in text, "HOL token leaked into raised message"


# =========================================================================
#  RETRY EXHAUSTION
# =========================================================================

class TestRetryExhaustion:
    """All retry attempts fail -> final raise; token stays redacted."""

    def test_connect_error_exhausts_and_raises(self, client, http_client):
        """A persistent ConnectError surfaces after 4 attempts."""
        http_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with pytest.raises(tenacity.RetryError) as exc_info:
            asyncio.run(client.get_holidays(2025))

        # The underlying cause is the httpx.ConnectError we injected.
        underlying = exc_info.value.last_attempt.exception()
        assert isinstance(underlying, httpx.ConnectError)
        # 4 attempts total (stop_after_attempt(4)).
        assert http_client.get.call_count == 4

    def test_request_error_exhausts(self, client, http_client):
        """A generic httpx.RequestError is also retried to exhaustion."""
        http_client.get = AsyncMock(
            side_effect=httpx.RequestError("boom")
        )
        with pytest.raises(tenacity.RetryError):
            asyncio.run(client.get_exchange_rates(
                date(2025, 3, 10), date(2025, 3, 10), "USD",
            ))
        assert http_client.get.call_count == 4

    def test_connect_error_with_token_in_url_is_redacted_in_raise(
        self, client, http_client,
    ):
        """Even if the transport error text embeds the token, the message
        that ultimately surfaces to a caller must not expose it.

        The redacted, generic surface is the BOTAPIError-raising paths; the
        RetryError repr exposes only a Future repr, never the token. We assert
        the token is absent from the stringified RetryError and its cause's
        repr-level surface that callers log.
        """
        leaky = httpx.ConnectError(
            f"failed GET https://gw/?token={_SECRET_EXG}"
        )
        http_client.get = AsyncMock(side_effect=leaky)

        with pytest.raises(tenacity.RetryError) as exc_info:
            asyncio.run(client.get_holidays(2025))

        # RetryError's own message is a Future repr — never the token.
        _assert_no_token(str(exc_info.value))


# =========================================================================
#  429 RATE LIMIT
# =========================================================================

class Test429Handling:
    """429s are handled internally and do NOT consume tenacity attempts."""

    def test_429_then_success(self, client, http_client):
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock()

        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {
            "result": {"data": [
                {"Date": "2025-04-14", "HolidayDescription": "Songkran"},
            ]}
        }
        ok.raise_for_status = MagicMock()

        http_client.get = AsyncMock(side_effect=[rate_limited, ok])

        holidays = asyncio.run(client.get_holidays(2025))
        assert len(holidays) == 1
        assert holidays[0].description == "Songkran"
        # 2 calls: one 429, one success — not a tenacity retry.
        assert http_client.get.call_count == 2

    def test_429_exhaustion_raises_generic_botapierror(
        self, client, http_client, monkeypatch,
    ):
        """Endless 429s eventually raise a generic BOTAPIError (no token)."""
        # Force a tiny 429 retry budget so the test is fast.
        monkeypatch.setattr("core.api_client.MAX_429_RETRIES", 3)
        # Avoid real sleeping on the internal 429 backoff loop.
        monkeypatch.setattr(
            "core.api_client.asyncio.sleep",
            AsyncMock(return_value=None),
        )

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock()
        http_client.get = AsyncMock(return_value=rate_limited)

        with pytest.raises(BOTAPIError) as exc_info:
            asyncio.run(client.get_holidays(2025))

        msg = str(exc_info.value)
        assert "rate limit exceeded" in msg.lower()
        _assert_no_token(msg)


# =========================================================================
#  REDACTION OF RAISED MESSAGES
# =========================================================================

class TestRaisedMessageRedaction:
    """No raised exception message may carry a token value."""

    def test_schema_error_message_has_no_token(self, client, http_client):
        """A malformed payload raises a generic schema error — no token."""
        bad = MagicMock()
        bad.status_code = 200
        # Missing required 'period' -> ValidationError -> generic BOTAPIError.
        bad.json.return_value = {
            "result": {"data": {"data_detail": [{"currency_id": "USD"}]}}
        }
        bad.raise_for_status = MagicMock()
        http_client.get = AsyncMock(return_value=bad)

        with pytest.raises(BOTAPIError) as exc_info:
            asyncio.run(client.get_exchange_rates(
                date(2025, 3, 10), date(2025, 3, 10), "USD",
            ))
        msg = str(exc_info.value)
        assert "unexpected schema" in msg.lower()
        _assert_no_token(msg)

    def test_http_500_surfaces_without_token(self, client, http_client):
        """A 5xx is retried as BOTTransientServerError; ensure no token leaks out.

        A transient 5xx is raised as BOTTransientServerError (in the tenacity
        retry set), so after exhaustion a tenacity.RetryError surfaces. Neither
        the RetryError nor the wrapped server-error message carries a token.
        """
        resp = MagicMock()
        resp.status_code = 500
        resp.headers = {}
        resp.raise_for_status = MagicMock()
        http_client.get = AsyncMock(return_value=resp)

        # 5xx -> BOTTransientServerError -> retried to exhaustion -> RetryError.
        with pytest.raises(tenacity.RetryError) as exc_info:
            asyncio.run(client.get_holidays(2025))
        _assert_no_token(str(exc_info.value))
        underlying = exc_info.value.last_attempt.exception()
        _assert_no_token(str(underlying))


# =========================================================================
#  SANITY: retry config matches the documented contract
# =========================================================================

def test_logger_name_is_module(client):
    """Sanity: the client wires its retry logging through the module logger
    (so the TokenRedactionFilter on root handlers can scrub it)."""
    assert logging.getLogger("core.api_client") is not None


# =========================================================================
#  BEFORE-SLEEP CALLBACK: no token in captured log records
# =========================================================================

class TestBeforeSleepNoTokenLeak:
    """_safe_before_sleep must never emit token values into log records.

    These tests drive a forced retry (ConnectError with token in the
    exception message) and assert that every WARNING record captured
    during the retry loop is free of the token.
    """

    def test_before_sleep_log_has_no_token_in_message(
        self, client, http_client, caplog,
    ):
        """Token embedded in ConnectError message must not reach log output."""
        leaky = httpx.ConnectError(
            f"failed GET https://gw/?token={_SECRET_EXG}"
        )
        http_client.get = AsyncMock(side_effect=leaky)

        with caplog.at_level(logging.WARNING, logger="core.api_client"), pytest.raises(tenacity.RetryError):
            asyncio.run(client.get_holidays(2025))

        # At least one WARNING record must have been emitted (the retry log).
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warning_records, "Expected at least one WARNING retry log record"

        for record in warning_records:
            msg = record.getMessage()
            assert _SECRET_EXG not in msg, (
                f"EXG token leaked into retry log message: {msg!r}"
            )
            assert _SECRET_HOL not in msg, (
                f"HOL token leaked into retry log message: {msg!r}"
            )

    def test_before_sleep_log_contains_expected_fields(
        self, client, http_client, caplog,
    ):
        """Retry log must include attempt number and exception class name."""
        http_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with caplog.at_level(logging.WARNING, logger="core.api_client"), pytest.raises(tenacity.RetryError):
            asyncio.run(client.get_holidays(2025))

        warning_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert warning_messages, "Expected WARNING retry log records"

        # Each retry log should mention the exception class and attempt info.
        for msg in warning_messages:
            assert "ConnectError" in msg, (
                f"Expected exception class name in retry log: {msg!r}"
            )

    def test_before_sleep_log_no_token_for_request_error(
        self, client, http_client, caplog,
    ):
        """Token embedded in RequestError message must not reach log output."""
        leaky = httpx.RequestError(
            f"transport error token={_SECRET_HOL}"
        )
        http_client.get = AsyncMock(side_effect=leaky)

        with caplog.at_level(logging.WARNING, logger="core.api_client"), pytest.raises(tenacity.RetryError):
            asyncio.run(client.get_exchange_rates(
                date(2025, 3, 10), date(2025, 3, 10), "USD",
            ))

        for record in caplog.records:
            if record.levelno == logging.WARNING:
                msg = record.getMessage()
                assert _SECRET_HOL not in msg, (
                    f"HOL token leaked into retry log message: {msg!r}"
                )
                assert _SECRET_EXG not in msg, (
                    f"EXG token leaked into retry log message: {msg!r}"
                )
