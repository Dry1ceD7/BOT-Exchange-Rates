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

        # ── Build Gimmick Layout ─────────────────────────────────────
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(pady=0, padx=6)

        # Left: USD
        self.usd_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.usd_frame.pack(side="left", padx=12, pady=4)

        self.lbl_usd_title = ctk.CTkLabel(
            self.usd_frame, text="🇺🇸 USD",
            font=ctk.CTkFont(weight="bold", size=13), text_color="#E2E8F0"
        )
        self.lbl_usd_title.pack(side="left", padx=(0, 8))

        self.lbl_usd_buy = ctk.CTkLabel(
            self.usd_frame, text="BUY --/--",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#94A3B8"
        )
        self.lbl_usd_buy.pack(side="left", padx=4)

        self.lbl_usd_sell = ctk.CTkLabel(
            self.usd_frame, text="SELL --/--",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#94A3B8"
        )
        self.lbl_usd_sell.pack(side="left", padx=4)

        # Center: LIVE indicator
        self.live_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.live_frame.pack(side="left", padx=20)
        self.lbl_live = ctk.CTkLabel(
            self.live_frame, text="● LIVE",
            font=ctk.CTkFont(size=9, weight="bold"), text_color="#ef4444"
        )
        self.lbl_live.pack(side="left", padx=(0, 4))
        self.lbl_time = ctk.CTkLabel(
            self.live_frame, text="--:--",
            font=ctk.CTkFont(size=9, weight="bold"), text_color="#94A3B8"
        )
        self.lbl_time.pack(side="left")

        # Right: EUR
        self.eur_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.eur_frame.pack(side="left", padx=12, pady=4)

        self.lbl_eur_title = ctk.CTkLabel(
            self.eur_frame, text="🇪🇺 EUR",
            font=ctk.CTkFont(weight="bold", size=13), text_color="#E2E8F0"
        )
        self.lbl_eur_title.pack(side="left", padx=(0, 8))

        self.lbl_eur_buy = ctk.CTkLabel(
            self.eur_frame, text="BUY --/--",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#94A3B8"
        )
        self.lbl_eur_buy.pack(side="left", padx=4)

        self.lbl_eur_sell = ctk.CTkLabel(
            self.eur_frame, text="SELL --/--",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#94A3B8"
        )
        self.lbl_eur_sell.pack(side="left", padx=4)

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
        except (OSError, ValueError, RuntimeError) as e:
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

        except (OSError, ValueError, RuntimeError) as e:
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
        usd_b_text, usd_b_color = self._format_single(usd_b, "usd_buying")
        usd_s_text, usd_s_color = self._format_single(usd_s, "usd_selling")

        # Format EUR
        eur_b = self._rates.get("eur_buying")
        eur_s = self._rates.get("eur_selling")
        eur_b_text, eur_b_color = self._format_single(eur_b, "eur_buying")
        eur_s_text, eur_s_color = self._format_single(eur_s, "eur_selling")

        self.lbl_usd_buy.configure(text=f"BUY {usd_b_text}", text_color=usd_b_color)
        self.lbl_usd_sell.configure(text=f"SELL {usd_s_text}", text_color=usd_s_color)

        self.lbl_eur_buy.configure(text=f"BUY {eur_b_text}", text_color=eur_b_color)
        self.lbl_eur_sell.configure(text=f"SELL {eur_s_text}", text_color=eur_s_color)

        self.lbl_time.configure(text=datetime.now().strftime("%H:%M:%S"))

        # Make the LIVE dot blink slightly between updates
        current_color = self.lbl_live.cget("text_color")
        self.lbl_live.configure(text_color="#ef4444" if current_color != "#ef4444" else "#dc2626")

    def _format_single(self, rate: Optional[Decimal], trend_key: str) -> tuple:
        if rate is None:
            return "--.--", "#64748B"

        val_str = f"{float(rate):.4f}"
        prev = self._prev_rates.get(trend_key)
        curr = self._rates.get(trend_key)

        if prev is not None and curr is not None and prev != curr:
            if curr > prev:
                return f"{val_str} ▲", "#22C55E"
            else:
                return f"{val_str} ▼", "#EF4444"
        return f"{val_str} ●", "#3B82F6"

    def apply_theme(self, theme: dict) -> None:
        """Re-apply colors for dark/light mode transitions."""
        text_primary = theme.get("header_text", "#FFFFFF")

        self.lbl_usd_title.configure(text_color=text_primary)
        self.lbl_eur_title.configure(text_color=text_primary)
