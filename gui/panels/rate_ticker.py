#!/usr/bin/env python3
"""
gui/panels/rate_ticker.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Live Rate Ticker Widget (PySide6)
---------------------------------------------------------------------------
Compact widget showing today's USD & EUR buying TT and selling rates.
Fetches from the BOT API on load and auto-refreshes every 30 minutes.
"""

import logging
import os
from datetime import date

import httpx
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

logger = logging.getLogger(__name__)

_BOT_GATEWAY = "https://gateway.api.bot.or.th"
_EXG_PATH = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"


class RateFetchWorker(QThread):
    """Background worker to fetch today's exchange rates."""
    done = Signal(dict)   # {"usd_buy": float, "usd_sell": float, ...}
    error = Signal(str)

    def run(self):
        try:
            token = os.environ.get("BOT_TOKEN_EXG", "")
            if not token:
                self.error.emit("No API token")
                return

            clean = token.removeprefix("Bearer ").strip()
            headers = {
                "X-IBM-Client-Id": clean,
                "Authorization": f"Bearer {clean}",
                "accept": "application/json",
            }

            today = date.today().strftime("%Y-%m-%d")
            rates = {}

            for currency in ("USD", "EUR"):
                url = (
                    f"{_BOT_GATEWAY}{_EXG_PATH}"
                    f"?start_period={today}&end_period={today}"
                    f"&currency={currency}"
                )
                resp = httpx.get(url, headers=headers, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    details = (
                        data.get("result", {})
                        .get("data", {})
                        .get("data_detail", [])
                    )
                    if details:
                        rec = details[-1]
                        key = currency.lower()
                        rates[f"{key}_buy"] = rec.get("buying_transfer")
                        rates[f"{key}_sell"] = rec.get("selling")

            if rates:
                self.done.emit(rates)
            else:
                self.error.emit("No data for today")
        except Exception as e:
            logger.warning("Rate ticker fetch failed: %s", e)
            self.error.emit(str(e))


class RateTickerWidget(QWidget):
    """
    Compact exchange rate ticker showing USD & EUR buy/sell.
    Designed to sit in the header/toolbar area.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_dark = True
        self._build_ui()
        self._setup_refresh()
        # Fetch on init
        self._fetch()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(0)

        self.lbl_rates = QLabel("Loading rates...")
        self.lbl_rates.setObjectName("RateTickerLabel")
        self.lbl_rates.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_rates)

    def _setup_refresh(self):
        """Auto-refresh every 30 minutes."""
        self._timer = QTimer(self)
        self._timer.setInterval(30 * 60 * 1000)
        self._timer.timeout.connect(self._fetch)
        self._timer.start()

    def _fetch(self):
        self._worker = RateFetchWorker(parent=self)
        self._worker.done.connect(self._on_rates)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def set_dark_mode(self, is_dark: bool):
        self._is_dark = is_dark
        self._apply_style()

    def _apply_style(self):
        if self._is_dark:
            self.lbl_rates.setStyleSheet(
                "font-size: 11px; font-weight: 600; "
                "color: #A6ADC8; background: transparent; "
                "padding: 2px 6px;"
            )
        else:
            self.lbl_rates.setStyleSheet(
                "font-size: 11px; font-weight: 600; "
                "color: #6C6F85; background: transparent; "
                "padding: 2px 6px;"
            )

    def _on_rates(self, rates: dict):
        usd_buy = rates.get("usd_buy")
        usd_sell = rates.get("usd_sell")
        eur_buy = rates.get("eur_buy")
        eur_sell = rates.get("eur_sell")

        parts = []
        if usd_buy is not None and usd_sell is not None:
            parts.append(f"USD  Buy {usd_buy:.2f} ┃ Sell {usd_sell:.2f}")
        if eur_buy is not None and eur_sell is not None:
            parts.append(f"EUR  Buy {eur_buy:.2f} ┃ Sell {eur_sell:.2f}")

        if parts:
            # Use colored text via stylesheet — green for buy, orange for sell
            if self._is_dark:
                buy_c, sell_c, sep_c = "#A6E3A1", "#FAB387", "#585B70"
                label_c = "#CDD6F4"
            else:
                buy_c, sell_c, sep_c = "#40A02B", "#FE640B", "#ACB0BE"
                label_c = "#4C4F69"

            html_parts = []
            for currency, buy_val, sell_val in [
                ("USD", usd_buy, usd_sell),
                ("EUR", eur_buy, eur_sell),
            ]:
                if buy_val is not None and sell_val is not None:
                    html_parts.append(
                        f'<span style="color:{label_c};font-weight:700;">{currency}</span> '
                        f'<span style="color:{buy_c};">Buy {buy_val:.2f}</span>'
                        f' <span style="color:{sep_c};">┃</span> '
                        f'<span style="color:{sell_c};">Sell {sell_val:.2f}</span>'
                    )

            separator = f'  <span style="color:{sep_c};">│</span>  '
            self.lbl_rates.setText(separator.join(html_parts))
        else:
            self.lbl_rates.setText("No rates available")

        self._apply_style()

    def _on_error(self, msg: str):
        self.lbl_rates.setText("Rates unavailable")
        self._apply_style()
