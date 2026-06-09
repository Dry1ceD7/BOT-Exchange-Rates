#!/usr/bin/env python3
"""
tests/test_api_live.py
---------------------------------------------------------------------------
Live BOT API smoke test — hits the real gateway with real tokens.

OPT-IN ONLY: skipped unless ``BOT_LIVE_TEST`` is set in the environment, so
CI (which has no BOT tokens) stays green. Run locally with::

    BOT_LIVE_TEST=1 ./.venv/bin/python -m pytest tests/test_api_live.py -v

It verifies that what the app fetches actually matches the official BOT
DAILY_AVG_EXG_RATE response — field names, schema parse fidelity, and basic
economic sanity (no token is ever printed; BOTClient registers redaction).
"""
import asyncio
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BOT_LIVE_TEST"),
    reason="live BOT API test — set BOT_LIVE_TEST=1 (needs real BOT tokens) to run",
)


def _fetch(currency: str, days: int = 21):
    """Fetch the last ``days`` of rates for ``currency`` via the app's client."""
    import httpx
    from dotenv import load_dotenv

    load_dotenv()  # keychain is tried first by get_token; .env is the fallback
    from core.api_client import CLIENT_TIMEOUT, BOTClient

    async def run():
        async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as c:
            client = BOTClient(c)
            end = date.today()
            start = end - timedelta(days=days)
            return await client.get_exchange_rates(start, end, currency)

    return asyncio.run(run())


def test_live_usd_rates_present_and_parse():
    """USD records come back and the schema parses them (field names match)."""
    recs = _fetch("USD")
    assert recs, "BOT returned no USD records for the recent window"
    r = recs[-1]
    # The schema fields populate => BOT's JSON keys match what the app expects.
    assert r.period
    assert r.currency == "USD"
    assert r.buying_transfer is not None
    assert r.selling is not None


def test_live_usd_rates_are_economically_sane():
    """Values sit in a sane THB/USD band and respect rate-type ordering."""
    recs = _fetch("USD")
    assert recs
    for r in recs:
        if r.buying_transfer is None or r.selling is None:
            continue
        # THB/USD has lived well within this band for decades.
        assert 10.0 < r.buying_transfer < 100.0, f"USD buy_tt out of band: {r}"
        # A bank sells foreign currency dearer than it buys it.
        assert r.selling >= r.buying_transfer, f"selling < buying: {r}"
        # Telegraphic-transfer buying is >= sight-bill buying.
        if r.buying_sight is not None:
            assert r.buying_transfer >= r.buying_sight, f"TT < sight: {r}"


def test_live_eur_rates_present():
    """EUR also resolves (second currency the ledger writes into fixed cols)."""
    recs = _fetch("EUR")
    assert recs, "BOT returned no EUR records for the recent window"
    assert recs[-1].buying_transfer is not None
