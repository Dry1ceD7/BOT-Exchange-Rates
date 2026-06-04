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
import random
import time
from datetime import date, timedelta

import httpx
from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.constants import (
    API_CONNECT_TIMEOUT_SECONDS,
    API_RETRY_ATTEMPTS,
    API_RETRY_BACKOFF_MAX_SECONDS,
    API_RETRY_BACKOFF_MIN_SECONDS,
    API_RETRY_BACKOFF_MULTIPLIER,
    API_TIMEOUT_SECONDS,
    MAX_429_RETRIES,
    RETRY_AFTER_MAX_SECONDS,
)

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# SECURITY: TOKEN REDACTION LOGGING FILTER
# -------------------------------------------------------------------------

class TokenRedactionFilter(logging.Filter):
    """Redact known BOT API token values from every log record.

    tenacity's before_sleep_log and traceback formatting can otherwise
    leak tokens (e.g. in request URLs/headers) into app.log or Sentry.
    This filter sources token values from three places so redaction holds
    even after the env→keychain migration scrubs os.environ:
      1. values explicitly registered by a live BOTClient instance,
      2. os.environ (legacy / pre-migration),
      3. the OS keychain via secure_tokens.get_token (post-migration).
    Any occurrence is replaced with '***' in the formatted message/args.
    """

    _REPLACEMENT = "***"

    # How long (seconds) a probed env/keychain result stays valid before the
    # next miss re-probes. Bounds keychain syscalls to at most one set per TTL
    # window instead of one set per log record (featherweight hot-path guard).
    _PROBE_TTL_SECONDS = 60.0

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        # Tokens registered by live clients (see BOTClient.__init__). Avoids
        # depending on os.environ once tokens migrate to the keychain.
        self._registered_tokens: set[str] = set()
        # Memoized env/keychain probe result + its expiry. Avoids hitting the
        # OS keychain on every log record (each get_token() can fire several
        # real keychain syscalls and even trigger interactive unlock prompts).
        self._probed_tokens: list[str] = []
        self._probe_expiry: float = 0.0

    def register_tokens(self, *tokens: str | None) -> None:
        """Register live token values so they are always redacted."""
        for tok in tokens:
            if tok:
                self._registered_tokens.add(tok)

    def _token_values(self) -> list:
        # Fast path: once a live client has registered its real token values
        # we already know what to redact — skip the env/keychain probe (and
        # its keychain syscalls) entirely. register_tokens() is called from
        # BOTClient.__init__, so this holds for the whole running app.
        if self._registered_tokens:
            return [v for v in self._registered_tokens if v]
        return list(self._probe_env_keychain())

    def _probe_env_keychain(self) -> list:
        """Memoized env/keychain lookup (TTL-bounded).

        Without registered client tokens we fall back to probing os.environ
        and — post-migration — the OS keychain. Those reads are expensive and
        can trigger interactive unlock prompts, so the result is cached for
        ``_PROBE_TTL_SECONDS`` instead of being re-queried per log record.
        """
        now = time.monotonic()
        if self._probed_tokens and now < self._probe_expiry:
            return self._probed_tokens

        import os
        values: list[str] = []
        for env_key in ("BOT_TOKEN_EXG", "BOT_TOKEN_HOL"):
            val = os.environ.get(env_key)
            if val:
                values.append(val)
            else:
                # Post-migration the secret lives only in the keychain.
                # Import locally to dodge an import cycle (secure_tokens does
                # not import api_client, but keep the call lazy regardless).
                try:
                    from core.secure_tokens import get_token
                    kc_val = get_token(env_key)
                except Exception:
                    kc_val = None
                if kc_val:
                    values.append(kc_val)
        self._probed_tokens = [v for v in values if v]
        self._probe_expiry = now + self._PROBE_TTL_SECONDS
        return self._probed_tokens

    def filter(self, record: logging.LogRecord) -> bool:
        tokens = self._token_values()
        if not tokens:
            return True
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for tok in tokens:
            if tok and tok in redacted:
                redacted = redacted.replace(tok, self._REPLACEMENT)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


# Module-level handle to the installed filter so a live BOTClient can
# register its actual token values for redaction (Fix: keychain-only tokens).
_active_redaction_filter: "TokenRedactionFilter | None" = None


def install_token_redaction_filter() -> None:
    """Attach the redaction filter to the root logger's handlers."""
    global _active_redaction_filter
    root = logging.getLogger()
    filt = TokenRedactionFilter()
    _active_redaction_filter = filt
    for handler in root.handlers:
        # Avoid attaching duplicates
        if not any(isinstance(f, TokenRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)


def register_redaction_tokens(*tokens: str | None) -> None:
    """Register live token values with the active redaction filter (if any).

    Safe no-op when the filter has not been installed (e.g. headless tests
    that construct a BOTClient without configuring root logging).
    """
    if _active_redaction_filter is not None:
        _active_redaction_filter.register_tokens(*tokens)


# -------------------------------------------------------------------------
# SECURE TENACITY RETRY CALLBACK
# -------------------------------------------------------------------------

def _safe_before_sleep(retry_state) -> None:
    """Log a retry attempt without exposing exception details or request context.

    Emits only: attempt count, exception class name, and wait time.
    Intentionally suppresses the exception message, which may embed a token
    (e.g. httpx.ConnectError with the full request URL in its str()).
    This eliminates the token-exposure vector present in tenacity's built-in
    before_sleep_log(), which logs ``str(exception)`` verbatim.
    """
    if retry_state.outcome is not None and retry_state.outcome.failed:
        ex_class = type(retry_state.outcome.exception()).__name__
    else:
        ex_class = "unknown"

    wait_secs = (
        retry_state.next_action.sleep if retry_state.next_action is not None else 0.0
    )
    logger.warning(
        "Retrying API request in %.3gs (attempt %d, %s)",
        wait_secs,
        retry_state.attempt_number,
        ex_class,
    )

# -------------------------------------------------------------------------
# PYDANTIC v2 SCHEMAS
# -------------------------------------------------------------------------

class BOTRateDetail(BaseModel):
    """Schema for a single day's exchange rate data point."""
    period: str
    currency: str = Field(alias="currency_id")
    buying_transfer: float | None = None
    buying_sight: float | None = None
    selling: float | None = None
    mid_rate: float | None = None

class BOTRateData(BaseModel):
    data_detail: list[BOTRateDetail]

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
    data: list[BOTHolidayDetail]

class BOTHolidayResponse(BaseModel):
    """Master schema for the BOT Holiday API payload."""
    result: BOTHolidayResult

# -------------------------------------------------------------------------
# EXCEPTION HANDLING
# -------------------------------------------------------------------------

class BOTAPIError(Exception):
    """Custom exception raised when the BOT API fails."""
    pass

class BOTTransientServerError(Exception):
    """Transient 5xx response — eligible for tenacity retry with backoff.

    Distinct from BOTAPIError so the retry policy targets only server-side
    failures; 4xx client errors continue to fail fast via raise_for_status.
    """
    pass

# -------------------------------------------------------------------------
# ASYNC API CLIENT
# -------------------------------------------------------------------------

# Recommended timeout for httpx.AsyncClient constructor (built from the
# centralized constants so there is a single source of truth).
CLIENT_TIMEOUT = httpx.Timeout(
    API_TIMEOUT_SECONDS, connect=API_CONNECT_TIMEOUT_SECONDS,
)


class BOTClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

        # v3.2.2: Retrieve tokens from OS keychain first, .env fallback
        from core.secure_tokens import get_token
        self.token_exg = get_token("BOT_TOKEN_EXG")
        self.token_hol = get_token("BOT_TOKEN_HOL")

        if not self.token_exg or not self.token_hol:
            raise BOTAPIError("Missing BOT API tokens.")

        # SECURITY: register the live token values with the active log
        # redaction filter so they are scrubbed even when sourced from the
        # keychain (os.environ may have been emptied post-migration).
        register_redaction_tokens(self.token_exg, self.token_hol)

        # Resolve the per-request read timeout from user settings, falling
        # back to the centralized constant. Connect timeout stays constant.
        self.timeout_seconds = self._resolve_timeout_seconds()

        self.gateway = "https://gateway.api.bot.or.th"
        self.exg_path = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
        self.hol_path = "/financial-institutions-holidays/"

    @staticmethod
    def _resolve_timeout_seconds() -> float:
        """Read 'api_timeout_seconds' from settings; constant on any failure."""
        try:
            from core.config_manager import SettingsManager
            value = SettingsManager().get(
                "api_timeout_seconds", API_TIMEOUT_SECONDS,
            )
            timeout = float(value)
            if timeout > 0:
                return timeout
        except Exception:  # noqa: S110 — deliberate: any failure falls back to the constant below
            pass
        return API_TIMEOUT_SECONDS

    @retry(
        stop=stop_after_attempt(API_RETRY_ATTEMPTS),
        wait=wait_exponential(
            multiplier=API_RETRY_BACKOFF_MULTIPLIER,
            min=API_RETRY_BACKOFF_MIN_SECONDS,
            max=API_RETRY_BACKOFF_MAX_SECONDS,
        ),
        retry=retry_if_exception_type((
            httpx.RequestError, httpx.ConnectError,
            httpx.TimeoutException, BOTTransientServerError,
        )),
        before_sleep=_safe_before_sleep,
    )
    async def _fetch_json(self, url: str, token: str) -> dict:
        """Fetch JSON from BOT API with built-in 429 rate limit handling.

        429 responses are handled internally (infinite loop with backoff)
        and do NOT consume tenacity retry attempts. Tenacity retries on
        real connection/timeout errors and on transient 5xx server errors
        (raised as BOTTransientServerError) with exponential backoff.
        """
        clean_token = token.removeprefix("Bearer ").strip()
        headers = {
            "X-IBM-Client-Id": clean_token,
            "Authorization": f"Bearer {clean_token}",
            "accept": "application/json"
        }

        max_retries = MAX_429_RETRIES
        for attempt_429 in range(max_retries):
            response = await self.client.get(
                url, headers=headers, timeout=self.timeout_seconds,
            )

            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {}
            if response.status_code == 429:
                retry_after = self._parse_retry_after(
                    response.headers.get("Retry-After"),
                )
                wait_time = retry_after + attempt_429  # escalating wait
                logger.warning(
                    "429 Rate limited (attempt %d/%d). Waiting %ds...",
                    attempt_429 + 1, max_retries, wait_time,
                )
                await asyncio.sleep(wait_time)
                continue

            # Transient 5xx → raise a retryable error so tenacity backs off
            # and retries (raise_for_status would raise a non-retryable
            # HTTPStatusError). 4xx still fails fast below.
            if 500 <= response.status_code < 600:
                raise BOTTransientServerError(
                    f"BOT API server error {response.status_code}."
                )

            # Any other error (4xx) → raise for caller, fail fast
            response.raise_for_status()

        raise BOTAPIError(
            f"BOT API rate limit exceeded after {max_retries} waits. "
            "Please try again in a few minutes."
        )

    @staticmethod
    def _parse_retry_after(raw: str | None) -> int:
        """Parse a 429 Retry-After header into a clamped integer seconds value.

        BOT returns a delta-seconds value, but RFC 7231 also permits an
        HTTP-date. Non-numeric values (dates) fall back to 5s rather than
        crashing; all values are clamped to RETRY_AFTER_MAX_SECONDS.
        """
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            seconds = 5
        if seconds < 0:
            seconds = 5
        return min(seconds, RETRY_AFTER_MAX_SECONDS)

    async def get_exchange_rates(
        self, start_date: date, end_date: date, currency: str,
    ) -> list[BOTRateDetail]:
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
                    # SECURITY: the ValidationError embeds the raw response
                    # body, which may contain echoed tokens/PII. Log the full
                    # detail only at DEBUG and raise a generic message.
                    logger.debug("BOT API schema validation failed: %s", e)
                    raise BOTAPIError(
                        "BOT API returned an unexpected schema."
                    ) from None
            current_start = current_end + timedelta(days=1)
            # Inter-chunk cooldown: 0.3-0.8s (429 handler protects against rate limiting)
            # noqa S311: jitter is timing-only, not security-sensitive — random is intentional.
            await asyncio.sleep(random.uniform(0.3, 0.8))  # noqa: S311
        return all_results

    async def get_holidays(self, year: int) -> list[BOTHolidayDetail]:
        url = f"{self.gateway}{self.hol_path}?year={year}"
        raw_json = await self._fetch_json(url, self.token_hol)
        if not raw_json or "result" not in raw_json:
            return []
        try:
            validated_response = BOTHolidayResponse.model_validate(raw_json)
            return validated_response.result.data
        except ValidationError as e:
            # SECURITY: the ValidationError embeds the raw response body,
            # which may contain echoed tokens/PII. Log the full detail only
            # at DEBUG and raise a generic message.
            logger.debug("BOT holiday API schema validation failed: %s", e)
            raise BOTAPIError(
                "BOT holiday API returned an unexpected schema."
            ) from None
