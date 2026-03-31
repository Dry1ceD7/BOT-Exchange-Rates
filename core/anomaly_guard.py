#!/usr/bin/env python3
"""
core/anomaly_guard.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Rate Anomaly Guardian
---------------------------------------------------------------------------
Detects suspicious rate jumps (±threshold%) between consecutive trading
days. Protects against BOT API glitches or data corruption before values
are written to financial ledgers.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    """Result of a single rate anomaly check."""
    is_anomaly: bool
    currency: str
    rate_type: str
    check_date: date
    new_value: Decimal
    prev_value: Optional[Decimal]
    pct_change: float
    message: str


class AnomalyGuard:
    """
    Validates exchange rate data by detecting abnormal day-over-day
    fluctuations. If a rate changes by more than ±threshold% from
    the previous available value, it is flagged as anomalous.

    Usage:
        guard = AnomalyGuard(threshold_pct=5.0)
        results = guard.check_rates_bulk(rates_dict)
        anomalies = [r for r in results if r.is_anomaly]
    """

    def __init__(self, threshold_pct: float = 5.0):
        """
        Args:
            threshold_pct: Maximum allowed day-over-day percentage
                           change before flagging as anomaly.
        """
        self.threshold_pct = threshold_pct

    def check_rate(
        self,
        currency: str,
        rate_type: str,
        check_date: date,
        new_value: Decimal,
        prev_value: Optional[Decimal],
    ) -> AnomalyResult:
        """
        Check a single rate against its previous value.

        Args:
            currency: Currency code (e.g., "USD").
            rate_type: Rate type label (e.g., "buying_transfer").
            check_date: The date of the new rate.
            new_value: The new rate value.
            prev_value: The previous trading day's rate (or None).

        Returns:
            AnomalyResult with is_anomaly=True if the rate change
            exceeds the threshold.
        """
        if prev_value is None or prev_value == 0:
            # No previous data to compare — cannot flag anomaly
            return AnomalyResult(
                is_anomaly=False,
                currency=currency,
                rate_type=rate_type,
                check_date=check_date,
                new_value=new_value,
                prev_value=prev_value,
                pct_change=0.0,
                message="No previous rate for comparison",
            )

        pct_change = float(
            abs(new_value - prev_value) / prev_value * 100
        )

        if pct_change > self.threshold_pct:
            msg = (
                f"ANOMALY: {currency} {rate_type} on "
                f"{check_date.strftime('%Y-%m-%d')} changed "
                f"{pct_change:.2f}% "
                f"({prev_value} → {new_value}). "
                f"Threshold: ±{self.threshold_pct}%"
            )
            logger.warning(msg)
            return AnomalyResult(
                is_anomaly=True,
                currency=currency,
                rate_type=rate_type,
                check_date=check_date,
                new_value=new_value,
                prev_value=prev_value,
                pct_change=pct_change,
                message=msg,
            )

        return AnomalyResult(
            is_anomaly=False,
            currency=currency,
            rate_type=rate_type,
            check_date=check_date,
            new_value=new_value,
            prev_value=prev_value,
            pct_change=pct_change,
            message="OK",
        )

    def check_rates_bulk(
        self,
        rates: Dict[str, Dict[date, Decimal]],
    ) -> List[AnomalyResult]:
        """
        Check all rates in a bulk dictionary for anomalies.

        Args:
            rates: Dict keyed by "{currency}_{rate_type}" mapping to
                   {date: Decimal}. Example:
                   {"USD_buying_transfer": {date(2025,1,2): Decimal("34.50"), ...}}

        Returns:
            List of AnomalyResult for every anomaly detected.
        """
        anomalies: List[AnomalyResult] = []

        for key, date_rates in rates.items():
            parts = key.split("_", 1)
            if len(parts) != 2:
                continue
            currency, rate_type = parts[0], parts[1]

            sorted_dates = sorted(date_rates.keys())
            prev_val: Optional[Decimal] = None

            for d in sorted_dates:
                val = date_rates[d]
                if val is None:
                    continue

                result = self.check_rate(
                    currency=currency,
                    rate_type=rate_type,
                    check_date=d,
                    new_value=val,
                    prev_value=prev_val,
                )
                if result.is_anomaly:
                    anomalies.append(result)

                prev_val = val

        return anomalies
