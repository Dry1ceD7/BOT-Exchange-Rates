#!/usr/bin/env python3
"""
core/currency_registry.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Currency Registry (OCP-compliant)
---------------------------------------------------------------------------
Centralised registry of supported currencies and their API field mappings.
To add a new currency (e.g., GBP), add ONE entry to CURRENCY_REGISTRY
instead of editing 3+ files.

Usage:
    from core.currency_registry import CURRENCY_REGISTRY, get_currency

    reg = get_currency("USD")
    print(reg.api_field)  # "buying_transfer"
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CurrencySpec:
    """Specification for a supported currency."""

    code: str
    """ISO 4217 currency code (e.g., 'USD')."""

    display_name: str
    """Human-readable name for UI display."""

    api_fields: Dict[str, str]
    """Mapping of UI label → BOT API field name.
    Example: {"Buying TT": "buying_transfer", "Selling": "selling"}
    """

    is_base: bool = False
    """True for base currency (THB) — always returns 1.0000."""

    is_core: bool = False
    """True for core currencies (USD, EUR) required for ledger processing."""


# ── Registry ─────────────────────────────────────────────────────────────
# To add a new currency, add a single CurrencySpec entry here.
# No other files need modification for basic ExRate sheet support.

CURRENCY_REGISTRY: List[CurrencySpec] = [
    CurrencySpec(
        code="THB",
        display_name="Thai Baht",
        api_fields={},
        is_base=True,
    ),
    CurrencySpec(
        code="USD",
        display_name="US Dollar",
        api_fields={
            "Buying TT": "buying_transfer",
            "Buying Sight": "buying_sight",
            "Selling": "selling",
            "Mid Rate": "mid_rate",
        },
        is_core=True,
    ),
    CurrencySpec(
        code="EUR",
        display_name="Euro",
        api_fields={
            "Buying TT": "buying_transfer",
            "Buying Sight": "buying_sight",
            "Selling": "selling",
            "Mid Rate": "mid_rate",
        },
        is_core=True,
    ),
    CurrencySpec(
        code="GBP",
        display_name="British Pound",
        api_fields={
            "Buying TT": "buying_transfer",
            "Selling": "selling",
            "Mid Rate": "mid_rate",
        },
    ),
    CurrencySpec(
        code="JPY",
        display_name="Japanese Yen",
        api_fields={
            "Buying TT": "buying_transfer",
            "Selling": "selling",
            "Mid Rate": "mid_rate",
        },
    ),
    CurrencySpec(
        code="CNY",
        display_name="Chinese Yuan",
        api_fields={
            "Buying TT": "buying_transfer",
            "Selling": "selling",
            "Mid Rate": "mid_rate",
        },
    ),
]

# ── Lookup helpers ────────────────────────────────────────────────────────

_REGISTRY_MAP: Dict[str, CurrencySpec] = {c.code: c for c in CURRENCY_REGISTRY}


def get_currency(code: str) -> Optional[CurrencySpec]:
    """Look up a CurrencySpec by ISO code (case-insensitive)."""
    return _REGISTRY_MAP.get(code.upper().strip())


def core_currencies() -> List[CurrencySpec]:
    """Return only the core currencies required for ledger processing."""
    return [c for c in CURRENCY_REGISTRY if c.is_core]


def all_currency_codes() -> List[str]:
    """Return all registered currency codes (excluding THB base)."""
    return [c.code for c in CURRENCY_REGISTRY if not c.is_base]
