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
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal

import customtkinter as ctk

from core.i18n import tr
from gui.panels._base_panel import SafePanel
from gui.theme import MONO_FONT, get_theme

logger = logging.getLogger(__name__)

# Placeholder shown before the first successful fetch (and for any rate that is
# unavailable). Matches the live single-value shape "34.2100" — four decimals,
# no slash — so the empty state reads as "loading one number" rather than
# implying two slash-separated values. Unified across init + _format_single.
PLACEHOLDER = "--.----"

# Bright placeholder color. The ticker sits on the navy header_bg in BOTH
# appearance modes, but ticker_muted is tuned for card_bg (white in light
# mode), so on the navy header it falls to ~1.5:1 in light mode. #CBD5E1
# reaches 8.18:1 (dark header) / 5.96:1 (light header) — clearly readable as
# a "loading" state. Themed via the optional "ticker_placeholder" token with
# this value as the .get() fallback until the theme defines it.
PLACEHOLDER_COLOR = "#CBD5E1"


class RateTicker(SafePanel, ctk.CTkFrame):
    """
    Compact live rate display for the header bar.

    Shows: USD ▲34.2100 / 34.5200  |  EUR ▲37.8300 / 38.1500
    Green = rate went up, Red = down, Gray = unchanged/unavailable.
    """

    REFRESH_MS = 60_000  # 60 seconds

    def __init__(self, master, cache_db=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._cache = cache_db
        self._rates: dict[str, Decimal | None] = {
            "usd_buying": None,
            "usd_selling": None,
            "eur_buying": None,
            "eur_selling": None,
        }
        self._prev_rates: dict[str, Decimal | None] = dict(self._rates)
        # True once the first successful _update_display has painted real data.
        # Until then the indicator shows a "connecting" state instead of LIVE,
        # so an empty cache never implies a live connection (finding #3).
        self._first_paint_done = False

        t = get_theme()

        # ── Build Gimmick Layout ─────────────────────────────────────
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(pady=0, padx=6)

        # Left: USD
        self.usd_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.usd_frame.pack(side="left", padx=12, pady=4)

        self.lbl_usd_title = ctk.CTkLabel(
            self.usd_frame, text="USD",
            font=ctk.CTkFont(weight="bold", size=13), text_color=t["ticker_value"]
        )
        self.lbl_usd_title.pack(side="left", padx=(0, 8))

        self.lbl_usd_buy = ctk.CTkLabel(
            self.usd_frame, text=f"BUY {PLACEHOLDER}",
            font=ctk.CTkFont(family=MONO_FONT, size=12, weight="bold"),
            text_color=t.get("ticker_placeholder", PLACEHOLDER_COLOR)
        )
        self.lbl_usd_buy.pack(side="left", padx=4)

        self.lbl_usd_sell = ctk.CTkLabel(
            self.usd_frame, text=f"SELL {PLACEHOLDER}",
            font=ctk.CTkFont(family=MONO_FONT, size=12, weight="bold"),
            text_color=t.get("ticker_placeholder", PLACEHOLDER_COLOR)
        )
        self.lbl_usd_sell.pack(side="left", padx=4)

        # Unicode mini-sparkline of the last few cached USD selling rates.
        self.lbl_usd_spark = ctk.CTkLabel(
            self.usd_frame, text="",
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            text_color=t["ticker_neutral"]
        )
        self.lbl_usd_spark.pack(side="left", padx=(2, 0))

        # Center: connection indicator. Starts in a "connecting" (amber)
        # state and only flips to "● LIVE" after the first real data paint —
        # an empty cache must never imply a live connection (finding #3).
        self.live_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.live_frame.pack(side="left", padx=20)
        self.lbl_live = ctk.CTkLabel(
            self.live_frame, text=tr("ticker.connecting"),
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=t.get("ticker_connecting", "#F59E0B")
        )
        self.lbl_live.pack(side="left", padx=(0, 4))
        self.lbl_time = ctk.CTkLabel(
            self.live_frame, text="--:--",
            font=ctk.CTkFont(size=9, weight="bold"), text_color=t["ticker_label"]
        )
        self.lbl_time.pack(side="left")

        # Right: EUR
        self.eur_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.eur_frame.pack(side="left", padx=12, pady=4)

        self.lbl_eur_title = ctk.CTkLabel(
            self.eur_frame, text="EUR",
            font=ctk.CTkFont(weight="bold", size=13), text_color=t["ticker_value"]
        )
        self.lbl_eur_title.pack(side="left", padx=(0, 8))

        self.lbl_eur_buy = ctk.CTkLabel(
            self.eur_frame, text=f"BUY {PLACEHOLDER}",
            font=ctk.CTkFont(family=MONO_FONT, size=12, weight="bold"),
            text_color=t.get("ticker_placeholder", PLACEHOLDER_COLOR)
        )
        self.lbl_eur_buy.pack(side="left", padx=4)

        self.lbl_eur_sell = ctk.CTkLabel(
            self.eur_frame, text=f"SELL {PLACEHOLDER}",
            font=ctk.CTkFont(family=MONO_FONT, size=12, weight="bold"),
            text_color=t.get("ticker_placeholder", PLACEHOLDER_COLOR)
        )
        self.lbl_eur_sell.pack(side="left", padx=4)

        # Unicode mini-sparkline of the last few cached EUR selling rates.
        self.lbl_eur_spark = ctk.CTkLabel(
            self.eur_frame, text="",
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            text_color=t["ticker_neutral"]
        )
        self.lbl_eur_spark.pack(side="left", padx=(2, 0))


    def start(self) -> None:
        """Begin the refresh cycle with a single persistent worker thread."""
        self._stop_event = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="RateTickerWorker",
        )
        self._worker.start()

    def stop(self) -> None:
        """Signal the worker thread to stop and wait for clean exit."""
        self._destroyed = True
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        if hasattr(self, "_worker") and self._worker.is_alive():
            self._worker.join(timeout=3)
            if self._worker.is_alive():
                logger.warning("RateTicker worker did not exit within 3s")

    def _worker_loop(self) -> None:
        """Persistent background thread: fetch rates on a timer."""
        while not self._stop_event.is_set():
            self._fetch_rates_bg()
            # Wait for REFRESH_MS or until stop is signaled
            self._stop_event.wait(timeout=self.REFRESH_MS / 1000.0)

    def _fetch_rates_bg(self) -> None:
        """Background thread: read from cache, fallback to API."""
        try:
            rates = self._read_from_cache()
            if rates:
                self._safe_after(0, self._update_display, rates)
                return
            # Try a lightweight API fetch for today only
            api_rates = self._fetch_today_from_api()
            if api_rates:
                self._safe_after(0, self._update_display, api_rates)
            elif not self._first_paint_done:
                # No cache, no API, and nothing has ever painted: surface an
                # explicit offline/dim state instead of a stale "connecting"
                # spinner that never resolves (finding #3).
                self._safe_after(0, self._show_offline)
        except (OSError, ValueError, RuntimeError) as e:
            logger.debug("Rate ticker refresh failed: %s", e)

    def _show_offline(self) -> None:
        """Tk-thread: switch the indicator to a dim 'offline' state."""
        if self._first_paint_done:
            return  # data arrived between scheduling and firing — leave LIVE
        t = get_theme()
        self.lbl_live.configure(
            text=tr("ticker.offline"),
            text_color=t.get("ticker_placeholder", PLACEHOLDER_COLOR),
        )

    def _read_from_cache(self) -> dict | None:
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

    def _fetch_today_from_api(self) -> dict | None:
        """Lightweight API call to get today's rates."""
        try:
            import httpx

            from core.secure_tokens import get_token
            token = get_token("BOT_TOKEN_EXG")
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
                # 5s per call (was 8s): caps worst-case first-paint latency at
                # ~10s for the two sequential currency requests (finding #3).
                resp = httpx.get(url, headers=headers, timeout=5.0)
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
                        try:
                            if bt is not None and str(bt).strip():
                                results[f"{prefix}_buying"] = Decimal(str(bt))
                            if sl is not None and str(sl).strip():
                                results[f"{prefix}_selling"] = Decimal(str(sl))
                        except Exception:
                            logger.debug("Invalid rate value for %s: bt=%r sl=%r", ccy, bt, sl)

            return results if results else None

        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as e:
            logger.debug("Ticker API fetch failed: %s", e)
            return None

    def _update_display(self, rates: dict) -> None:
        """Update the labels with fresh rate data."""
        # Update only the keys actually present in this fetch. Snapshot prev
        # for each key against the value present BEFORE this key's update, so
        # a partial dict can't reset trend arrows for untouched currencies.
        for key in self._rates:
            if key in rates and rates[key] is not None:
                self._prev_rates[key] = self._rates[key]
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

        # Mark first paint and refresh the sparkline from cache (cheap, cached).
        self._first_paint_done = True
        self._update_sparklines()

        # Flip to "● LIVE" now that real data has arrived; thereafter make the
        # dot blink slightly between updates. Before first paint the indicator
        # is left in its amber "connecting" state by start()/__init__.
        t = get_theme()
        current_color = self.lbl_live.cget("text_color")
        live_color = (
            t["ticker_live"] if current_color != t["ticker_live"]
            else t["ticker_live_alt"]
        )
        self.lbl_live.configure(text=tr("ticker.live"), text_color=live_color)

    def _update_sparklines(self) -> None:
        """Render a unicode mini-sparkline of recent cached selling rates.

        Reads the last ~7 calendar days of USD/EUR selling rates straight from
        the cache (no network, no history table growth) and maps them onto the
        8-level block ramp. Bounded to a handful of rows; called only from the
        Tk thread inside _update_display. Failures degrade to a blank label.
        """
        if self._cache is None:
            return
        t = get_theme()
        try:
            end = date.today()
            start = end - timedelta(days=7)
            bulk = self._cache.get_rates_bulk(start, end)
        except (OSError, ValueError, RuntimeError) as e:
            logger.debug("Sparkline cache read failed: %s", e)
            return

        for prefix, label in (
            ("usd_selling", self.lbl_usd_spark),
            ("eur_selling", self.lbl_eur_spark),
        ):
            series = [
                bulk[d][prefix]
                for d in sorted(bulk)
                if bulk[d].get(prefix) is not None
            ]
            label.configure(
                text=self._sparkline(series),
                text_color=t["ticker_neutral"],
            )

    @staticmethod
    def _sparkline(values: list) -> str:
        """Map a numeric series onto the 8-level unicode block ramp.

        Returns "" for fewer than 2 points (no trend to show). A flat series
        (all equal) renders as a mid-level baseline rather than dividing by
        zero. Decimal-safe: arithmetic stays in Decimal/float space only for
        the normalization ratio.
        """
        bars = "▁▂▃▄▅▆▇█"
        if not values or len(values) < 2:
            return ""
        floats = [float(v) for v in values]
        lo, hi = min(floats), max(floats)
        span = hi - lo
        if span <= 0:
            return bars[len(bars) // 2] * len(floats)
        out = []
        last = len(bars) - 1
        for f in floats:
            idx = int((f - lo) / span * last)
            out.append(bars[max(0, min(last, idx))])
        return "".join(out)

    def _format_single(self, rate: Decimal | None, trend_key: str) -> tuple:
        t = get_theme()
        if rate is None:
            # Same token + bright placeholder color as the initial empty state,
            # so an unavailable rate reads identically to "still loading".
            return PLACEHOLDER, t.get("ticker_placeholder", PLACEHOLDER_COLOR)

        val_str = f"{float(rate):.4f}"
        prev = self._prev_rates.get(trend_key)
        curr = self._rates.get(trend_key)

        if prev is not None and curr is not None and prev != curr:
            if curr > prev:
                return f"{val_str} ▲", t["ticker_up"]
            else:
                return f"{val_str} ▼", t["ticker_down"]
        return f"{val_str} ●", t["ticker_neutral"]

    def apply_theme(self, theme: dict) -> None:
        """Re-apply colors for dark/light mode transitions."""
        self.lbl_usd_title.configure(text_color=theme.get("ticker_value", "#FFFFFF"))
        self.lbl_eur_title.configure(text_color=theme.get("ticker_value", "#FFFFFF"))

        # Before the first data paint the rate labels hold the bright
        # placeholder color; after it they carry per-rate trend colors that a
        # blanket ticker_label repaint would wipe out. Only recolor the rate
        # labels while still in the empty placeholder state.
        if not self._first_paint_done:
            placeholder = theme.get("ticker_placeholder", PLACEHOLDER_COLOR)
            for lbl in (
                self.lbl_usd_buy, self.lbl_usd_sell,
                self.lbl_eur_buy, self.lbl_eur_sell,
            ):
                lbl.configure(text_color=placeholder)
            # Indicator still in the connecting/offline state.
            self.lbl_live.configure(
                text_color=theme.get("ticker_connecting", "#F59E0B")
            )
        else:
            self.lbl_live.configure(text_color=theme.get("ticker_live", "#ef4444"))

        # Sparklines follow the neutral trend color in either state.
        neutral = theme.get("ticker_neutral", "#3B82F6")
        self.lbl_usd_spark.configure(text_color=neutral)
        self.lbl_eur_spark.configure(text_color=neutral)
        self.lbl_time.configure(text_color=theme.get("ticker_label", "#94A3B8"))
