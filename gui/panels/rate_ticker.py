#!/usr/bin/env python3
"""
gui/panels/rate_ticker.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Live Rate Ticker Widget
---------------------------------------------------------------------------
Compact header-bar widget displaying today's USD and EUR Buying TT
and Selling rates. Fetches from CacheDB with a lightweight API
fallback on startup. Auto-refreshes every 60 seconds.
"""

import logging
import os
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)


class RateTicker(ctk.CTkFrame):
    """
    Compact live rate display for the header bar.

    Shows: USD ▲34.2100 / 34.5200  |  EUR ▲37.8300 / 38.1500
    Green = rate went up, Red = down, Gray = unchanged/unavailable.
    """

    REFRESH_MS = 60_000  # 60 seconds

    def __init__(self, master, cache_db=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._cache = cache_db
        self._rates: Dict[str, Optional[Decimal]] = {
            "usd_buying": None,
            "usd_selling": None,
            "eur_buying": None,
            "eur_selling": None,
        }
        self._prev_rates: Dict[str, Optional[Decimal]] = dict(self._rates)

        # ── Build compact layout ─────────────────────────────────────
        self._lbl_usd = ctk.CTkLabel(
            self, text="USD --/--",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color="#94A3B8",
        )
        self._lbl_usd.pack(side="left", padx=(4, 2))

        self._lbl_sep = ctk.CTkLabel(
            self, text="|",
            font=ctk.CTkFont(size=10),
            text_color="#475569",
        )
        self._lbl_sep.pack(side="left", padx=2)

        self._lbl_eur = ctk.CTkLabel(
            self, text="EUR --/--",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color="#94A3B8",
        )
        self._lbl_eur.pack(side="left", padx=(2, 4))

        self._lbl_time = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=8),
            text_color="#64748B",
        )
        self._lbl_time.pack(side="left", padx=(4, 0))

    def start(self) -> None:
        """Begin the refresh cycle."""
        self._refresh()

    def _refresh(self) -> None:
        """Fetch latest rates and update display."""
        threading.Thread(
            target=self._fetch_rates_bg, daemon=True,
        ).start()
        self.after(self.REFRESH_MS, self._refresh)

    def _fetch_rates_bg(self) -> None:
        """Background thread: read from cache, fallback to API."""
        try:
            rates = self._read_from_cache()
            if rates:
                self.after(0, self._update_display, rates)
            else:
                # Try a lightweight API fetch for today only
                api_rates = self._fetch_today_from_api()
                if api_rates:
                    self.after(0, self._update_display, api_rates)
        except Exception as e:
            logger.debug("Rate ticker refresh failed: %s", e)

    def _read_from_cache(self) -> Optional[Dict]:
        """Read today's (or last available) rates from CacheDB."""
        if self._cache is None:
            return None

        # Try today, then step back up to 5 days for weekends/holidays
        target = date.today()
        for _ in range(6):
            rate = self._cache.get_rate(target)
            if rate and any(v is not None for v in rate.values()):
                return rate
            target -= timedelta(days=1)
        return None

    def _fetch_today_from_api(self) -> Optional[Dict]:
        """Lightweight API call to get today's rates."""
        try:
            import httpx
            token = os.environ.get("BOT_TOKEN_EXG", "")
            if not token:
                return None

            clean_token = token.removeprefix("Bearer ").strip()
            headers = {
                "X-IBM-Client-Id": clean_token,
                "Authorization": f"Bearer {clean_token}",
                "accept": "application/json",
            }
            gateway = "https://gateway.api.bot.or.th"
            today_str = date.today().strftime("%Y-%m-%d")

            results = {}
            for ccy in ("USD", "EUR"):
                url = (
                    f"{gateway}/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
                    f"?start_period={today_str}"
                    f"&end_period={today_str}"
                    f"&currency={ccy}"
                )
                resp = httpx.get(url, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    details = (
                        data.get("result", {})
                        .get("data", {})
                        .get("data_detail", [])
                    )
                    if details:
                        rec = details[0]
                        prefix = ccy.lower()
                        bt = rec.get("buying_transfer")
                        sl = rec.get("selling")
                        if bt is not None:
                            results[f"{prefix}_buying"] = Decimal(str(bt))
                        if sl is not None:
                            results[f"{prefix}_selling"] = Decimal(str(sl))

            return results if results else None

        except Exception as e:
            logger.debug("Ticker API fetch failed: %s", e)
            return None

    def _update_display(self, rates: Dict) -> None:
        """Update the labels with fresh rate data."""
        # Store previous for delta coloring
        self._prev_rates = dict(self._rates)

        # Update current rates
        for key in self._rates:
            if key in rates and rates[key] is not None:
                self._rates[key] = rates[key]

        # Format USD
        usd_b = self._rates.get("usd_buying")
        usd_s = self._rates.get("usd_selling")
        usd_text, usd_color = self._format_pair("USD", usd_b, usd_s, "usd_buying")

        # Format EUR
        eur_b = self._rates.get("eur_buying")
        eur_s = self._rates.get("eur_selling")
        eur_text, eur_color = self._format_pair("EUR", eur_b, eur_s, "eur_buying")

        self._lbl_usd.configure(text=usd_text, text_color=usd_color)
        self._lbl_eur.configure(text=eur_text, text_color=eur_color)
        self._lbl_time.configure(
            text=datetime.now().strftime("%H:%M"),
        )

    def _format_pair(
        self, ccy: str,
        buying: Optional[Decimal],
        selling: Optional[Decimal],
        trend_key: str,
    ) -> tuple:
        """Format a currency pair for display."""
        if buying is None and selling is None:
            return f"{ccy} --/--", "#64748B"

        b_str = f"{float(buying):.4f}" if buying else "--"
        s_str = f"{float(selling):.4f}" if selling else "--"

        # Determine trend color
        prev = self._prev_rates.get(trend_key)
        curr = self._rates.get(trend_key)
        if prev is not None and curr is not None and prev != curr:
            if curr > prev:
                arrow = "▲"
                color = "#22C55E"  # green
            else:
                arrow = "▼"
                color = "#EF4444"  # red
        else:
            arrow = "●"
            color = "#3B82F6"  # blue/neutral

        return f"{ccy}{arrow}{b_str}/{s_str}", color

    def apply_theme(self, theme: dict) -> None:
        """Re-apply colors for dark/light mode transitions."""
        self._lbl_sep.configure(text_color=theme.get("text_muted", "#475569"))
        self._lbl_time.configure(text_color=theme.get("text_muted", "#64748B"))
