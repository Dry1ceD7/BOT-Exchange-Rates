#!/usr/bin/env python3
"""Tests for core/anomaly_guard.py — Rate Anomaly Detection."""

from datetime import date
from decimal import Decimal

from core.anomaly_guard import AnomalyGuard


class TestAnomalyGuardSingleCheck:
    """Test individual rate checks."""

    def test_normal_rate_no_anomaly(self):
        guard = AnomalyGuard(threshold_pct=5.0)
        result = guard.check_rate(
            currency="USD",
            rate_type="buying_transfer",
            check_date=date(2025, 3, 15),
            new_value=Decimal("34.5000"),
            prev_value=Decimal("34.4000"),
        )
        assert not result.is_anomaly
        assert result.pct_change < 5.0

    def test_large_jump_is_anomaly(self):
        guard = AnomalyGuard(threshold_pct=5.0)
        result = guard.check_rate(
            currency="EUR",
            rate_type="selling",
            check_date=date(2025, 3, 16),
            new_value=Decimal("40.0000"),
            prev_value=Decimal("37.0000"),  # ~8.1% increase
        )
        assert result.is_anomaly
        assert result.pct_change > 5.0

    def test_no_previous_value(self):
        """First observation should never be flagged."""
        guard = AnomalyGuard(threshold_pct=5.0)
        result = guard.check_rate(
            currency="USD",
            rate_type="buying_transfer",
            check_date=date(2025, 1, 2),
            new_value=Decimal("34.5000"),
            prev_value=None,
        )
        assert not result.is_anomaly

    def test_zero_previous_value(self):
        """Edge case: previous zero should not divide-by-zero."""
        guard = AnomalyGuard(threshold_pct=5.0)
        result = guard.check_rate(
            currency="USD",
            rate_type="buying_transfer",
            check_date=date(2025, 1, 2),
            new_value=Decimal("34.5000"),
            prev_value=Decimal("0"),
        )
        assert not result.is_anomaly

    def test_exact_threshold_not_anomaly(self):
        """A change of exactly 5% should still trigger (> not >=)."""
        guard = AnomalyGuard(threshold_pct=5.0)
        prev = Decimal("100.0000")
        new = Decimal("105.0001")  # Just over 5%
        result = guard.check_rate(
            currency="USD",
            rate_type="selling",
            check_date=date(2025, 6, 1),
            new_value=new,
            prev_value=prev,
        )
        assert result.is_anomaly

    def test_custom_threshold(self):
        """Verify custom threshold is respected."""
        guard = AnomalyGuard(threshold_pct=2.0)
        result = guard.check_rate(
            currency="EUR",
            rate_type="buying_transfer",
            check_date=date(2025, 7, 1),
            new_value=Decimal("38.0000"),
            prev_value=Decimal("37.0000"),  # ~2.7%
        )
        assert result.is_anomaly


class TestAnomalyGuardBulk:
    """Test bulk rate checking."""

    def test_bulk_no_anomalies(self):
        guard = AnomalyGuard(threshold_pct=5.0)
        rates = {
            "USD_buying_transfer": {
                date(2025, 1, 2): Decimal("34.00"),
                date(2025, 1, 3): Decimal("34.10"),
                date(2025, 1, 6): Decimal("34.05"),
            },
        }
        anomalies = guard.check_rates_bulk(rates)
        assert len(anomalies) == 0

    def test_bulk_detects_anomaly(self):
        guard = AnomalyGuard(threshold_pct=5.0)
        rates = {
            "USD_buying_transfer": {
                date(2025, 1, 2): Decimal("34.00"),
                date(2025, 1, 3): Decimal("34.10"),
                date(2025, 1, 6): Decimal("40.00"),  # ~17% jump
            },
        }
        anomalies = guard.check_rates_bulk(rates)
        assert len(anomalies) == 1
        assert anomalies[0].check_date == date(2025, 1, 6)

    def test_bulk_multiple_currencies(self):
        guard = AnomalyGuard(threshold_pct=5.0)
        rates = {
            "USD_buying_transfer": {
                date(2025, 1, 2): Decimal("34.00"),
                date(2025, 1, 3): Decimal("34.10"),
            },
            "EUR_selling": {
                date(2025, 1, 2): Decimal("37.00"),
                date(2025, 1, 3): Decimal("42.00"),  # ~13.5% jump
            },
        }
        anomalies = guard.check_rates_bulk(rates)
        assert len(anomalies) == 1
        assert anomalies[0].currency == "EUR"
