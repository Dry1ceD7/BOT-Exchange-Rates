#!/usr/bin/env python3
"""
tests/gui/test_rate_ticker.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/rate_ticker.py (RateTicker).

These tests exercise:
  1. Widget tree construction — expected child widgets are created at __init__.
  2. start() / stop() threading contract — _stop_event + _worker lifecycle.
  3. apply_theme() — re-applies label colors for dark/light mode transitions.
  4. _format_single() — returns correct string and color tuple for all trend
     cases (up, down, neutral, None/muted).
  5. SafePanel mixin — _destroyed flag lifecycle and _safe_after no-op.

All tests require a display; the tk_root fixture skips them on headless CI.
No real network calls, keyring access, or CacheDB queries are made.
"""

import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ticker(tk_root, cache_db=None):
    """Instantiate RateTicker with an optional mock cache_db."""
    from gui.panels.rate_ticker import RateTicker
    return RateTicker(tk_root, cache_db=cache_db)


# ---------------------------------------------------------------------------
# 1. Widget tree
# ---------------------------------------------------------------------------

class TestRateTickerWidgetTree:
    """RateTicker constructs the expected child widgets at __init__."""

    def test_panel_instantiates_without_error(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker is not None
        ticker.destroy()

    def test_container_frame_exists(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "container"), "container frame must exist"
        ticker.destroy()

    def test_lbl_usd_buy_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_usd_buy")
        assert isinstance(ticker.lbl_usd_buy, ctk.CTkLabel)
        ticker.destroy()

    def test_lbl_usd_sell_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_usd_sell")
        assert isinstance(ticker.lbl_usd_sell, ctk.CTkLabel)
        ticker.destroy()

    def test_lbl_eur_buy_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_eur_buy")
        assert isinstance(ticker.lbl_eur_buy, ctk.CTkLabel)
        ticker.destroy()

    def test_lbl_eur_sell_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_eur_sell")
        assert isinstance(ticker.lbl_eur_sell, ctk.CTkLabel)
        ticker.destroy()

    def test_lbl_time_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_time")
        assert isinstance(ticker.lbl_time, ctk.CTkLabel)
        ticker.destroy()

    def test_lbl_live_exists(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert hasattr(ticker, "lbl_live")
        assert isinstance(ticker.lbl_live, ctk.CTkLabel)
        ticker.destroy()

    def test_initial_usd_buy_text(self, tk_root):
        from gui.panels.rate_ticker import PLACEHOLDER
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_usd_buy.cget("text") == f"BUY {PLACEHOLDER}"
        ticker.destroy()

    def test_initial_usd_sell_text(self, tk_root):
        from gui.panels.rate_ticker import PLACEHOLDER
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_usd_sell.cget("text") == f"SELL {PLACEHOLDER}"
        ticker.destroy()

    def test_initial_eur_buy_text(self, tk_root):
        from gui.panels.rate_ticker import PLACEHOLDER
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_eur_buy.cget("text") == f"BUY {PLACEHOLDER}"
        ticker.destroy()

    def test_initial_eur_sell_text(self, tk_root):
        from gui.panels.rate_ticker import PLACEHOLDER
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_eur_sell.cget("text") == f"SELL {PLACEHOLDER}"
        ticker.destroy()

    def test_initial_time_text(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_time.cget("text") == "--:--"
        ticker.destroy()

    def test_cache_none_stored(self, tk_root):
        ticker = _make_ticker(tk_root, cache_db=None)
        assert ticker._cache is None
        ticker.destroy()

    def test_cache_mock_stored(self, tk_root):
        mock_cache = MagicMock()
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        assert ticker._cache is mock_cache
        ticker.destroy()

    def test_rates_dict_initialised_to_none(self, tk_root):
        ticker = _make_ticker(tk_root)
        for key in ("usd_buying", "usd_selling", "eur_buying", "eur_selling"):
            assert ticker._rates[key] is None, f"_rates[{key!r}] must start as None"
        ticker.destroy()


# ---------------------------------------------------------------------------
# 2. Threading — start() / stop()
# ---------------------------------------------------------------------------

class TestRateTickerThreading:
    """start() / stop() manage _stop_event and _worker correctly."""

    def test_start_creates_stop_event(self, tk_root):
        ticker = _make_ticker(tk_root)
        # Patch _fetch_rates_bg so the worker loop blocks on the event
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            assert hasattr(ticker, "_stop_event")
            assert isinstance(ticker._stop_event, threading.Event)
            ticker.stop()
        ticker.destroy()

    def test_start_creates_daemon_worker_thread(self, tk_root):
        ticker = _make_ticker(tk_root)
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            assert hasattr(ticker, "_worker")
            assert isinstance(ticker._worker, threading.Thread)
            assert ticker._worker.daemon is True
            ticker.stop()
        ticker.destroy()

    def test_worker_thread_named_correctly(self, tk_root):
        ticker = _make_ticker(tk_root)
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            assert ticker._worker.name == "RateTickerWorker"
            ticker.stop()
        ticker.destroy()

    def test_stop_sets_stop_event(self, tk_root):
        ticker = _make_ticker(tk_root)
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            ticker.stop()
            assert ticker._stop_event.is_set()
        ticker.destroy()

    def test_stop_joins_worker(self, tk_root):
        ticker = _make_ticker(tk_root)
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            ticker.stop()
            # After stop() + join the thread must be dead
            assert not ticker._worker.is_alive()
        ticker.destroy()

    def test_stop_without_start_is_safe(self, tk_root):
        """stop() must not raise if start() was never called."""
        ticker = _make_ticker(tk_root)
        ticker.stop()  # should not raise
        ticker.destroy()


# ---------------------------------------------------------------------------
# 3. apply_theme()
# ---------------------------------------------------------------------------

class TestRateTickerApplyTheme:
    """apply_theme() updates label colors for dark/light mode."""

    def test_apply_theme_dark_palette_empty_state(self, tk_root):
        """Before first paint the rate labels carry the placeholder color and
        the indicator stays in the connecting (amber) state."""
        from gui.panels.rate_ticker import PLACEHOLDER_COLOR
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        theme = get_theme()
        ticker.apply_theme(theme)  # must not raise
        placeholder = theme.get("ticker_placeholder", PLACEHOLDER_COLOR)
        assert ticker.lbl_usd_title.cget("text_color") == theme["ticker_value"]
        assert ticker.lbl_eur_title.cget("text_color") == theme["ticker_value"]
        assert ticker.lbl_usd_buy.cget("text_color") == placeholder
        assert ticker.lbl_usd_sell.cget("text_color") == placeholder
        assert ticker.lbl_eur_buy.cget("text_color") == placeholder
        assert ticker.lbl_eur_sell.cget("text_color") == placeholder
        # Connecting state, NOT live, since no data has painted yet.
        assert ticker.lbl_live.cget("text_color") == theme.get(
            "ticker_connecting", "#F59E0B"
        )
        assert ticker.lbl_time.cget("text_color") == theme["ticker_label"]
        ticker.destroy()

    def test_apply_theme_after_first_paint_keeps_live(self, tk_root):
        """After first paint apply_theme() must not wipe trend colors and the
        indicator follows ticker_live."""
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        ticker._first_paint_done = True
        theme = get_theme()
        ticker.apply_theme(theme)
        assert ticker.lbl_live.cget("text_color") == theme["ticker_live"]
        ticker.destroy()

    def test_apply_theme_custom_dict(self, tk_root):
        """apply_theme() uses dict.get() with defaults — custom dict is safe."""
        ticker = _make_ticker(tk_root)
        custom = {
            "ticker_value": "#AABBCC",
            "ticker_placeholder": "#DDEEFF",
            "ticker_connecting": "#FFAA00",
        }
        ticker.apply_theme(custom)
        assert ticker.lbl_usd_title.cget("text_color") == "#AABBCC"
        assert ticker.lbl_usd_buy.cget("text_color") == "#DDEEFF"
        assert ticker.lbl_live.cget("text_color") == "#FFAA00"
        ticker.destroy()

    def test_apply_theme_empty_dict_uses_defaults(self, tk_root):
        """apply_theme() falls back to hardcoded defaults for missing keys."""
        from gui.panels.rate_ticker import PLACEHOLDER_COLOR
        ticker = _make_ticker(tk_root)
        ticker.apply_theme({})  # all keys missing — should not raise
        assert ticker.lbl_usd_title.cget("text_color") == "#FFFFFF"
        # Placeholder fallback, not ticker_label.
        assert ticker.lbl_usd_buy.cget("text_color") == PLACEHOLDER_COLOR
        ticker.destroy()


# ---------------------------------------------------------------------------
# 4. _format_single()
# ---------------------------------------------------------------------------

class TestRateTickerFormatSingle:
    """_format_single() returns the right (text, color) tuple."""

    def _ticker_with_rates(self, tk_root, current, previous):
        """Return a ticker with _rates and _prev_rates pre-populated."""
        ticker = _make_ticker(tk_root)
        ticker._rates["usd_buying"] = current
        ticker._prev_rates["usd_buying"] = previous
        return ticker

    def test_none_rate_returns_placeholder(self, tk_root):
        """An unavailable rate renders the same bright placeholder token+color
        as the initial empty state (findings #1, #2)."""
        from gui.panels.rate_ticker import PLACEHOLDER, PLACEHOLDER_COLOR
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        text, color = ticker._format_single(None, "usd_buying")
        assert text == PLACEHOLDER
        assert color == get_theme().get("ticker_placeholder", PLACEHOLDER_COLOR)
        ticker.destroy()

    def test_rate_up_returns_up_color(self, tk_root):
        from gui.theme import get_theme
        ticker = self._ticker_with_rates(
            tk_root, Decimal("34.5000"), Decimal("34.2000")
        )
        text, color = ticker._format_single(Decimal("34.5000"), "usd_buying")
        assert "▲" in text
        assert color == get_theme()["ticker_up"]
        ticker.destroy()

    def test_rate_down_returns_down_color(self, tk_root):
        from gui.theme import get_theme
        ticker = self._ticker_with_rates(
            tk_root, Decimal("34.0000"), Decimal("34.5000")
        )
        text, color = ticker._format_single(Decimal("34.0000"), "usd_buying")
        assert "▼" in text
        assert color == get_theme()["ticker_down"]
        ticker.destroy()

    def test_rate_neutral_returns_neutral_color(self, tk_root):
        from gui.theme import get_theme
        ticker = self._ticker_with_rates(
            tk_root, Decimal("34.2500"), Decimal("34.2500")
        )
        text, color = ticker._format_single(Decimal("34.2500"), "usd_buying")
        assert "●" in text
        assert color == get_theme()["ticker_neutral"]
        ticker.destroy()

    def test_rate_no_previous_returns_neutral(self, tk_root):
        """When _prev_rates[key] is None, result is neutral (no trend)."""
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        # _prev_rates starts all None; _rates["usd_buying"] also None
        ticker._rates["usd_buying"] = Decimal("34.2500")
        # _prev_rates["usd_buying"] remains None
        text, color = ticker._format_single(Decimal("34.2500"), "usd_buying")
        assert "●" in text
        assert color == get_theme()["ticker_neutral"]
        ticker.destroy()

    def test_format_value_has_4dp(self, tk_root):
        ticker = _make_ticker(tk_root)
        text, _ = ticker._format_single(Decimal("34.25"), "usd_buying")
        # The value portion must contain 4 decimal places
        assert "34.2500" in text
        ticker.destroy()


# ---------------------------------------------------------------------------
# 5. SafePanel mixin contract
# ---------------------------------------------------------------------------

class TestRateTickerSafePanelMixin:
    """SafePanel mixin _destroyed flag + _safe_after behave correctly."""

    def test_destroyed_flag_starts_false(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker._destroyed is False
        ticker.destroy()

    def test_destroyed_flag_flips_on_destroy(self, tk_root):
        ticker = _make_ticker(tk_root)
        ticker.destroy()
        assert ticker._destroyed is True

    def test_safe_after_noop_post_destroy(self, tk_root):
        """_safe_after must silently no-op after destroy()."""
        ticker = _make_ticker(tk_root)
        ticker.destroy()

        called = []
        ticker._safe_after(0, lambda: called.append(1))
        assert called == [], "_safe_after must be a no-op post-destroy"

    def test_stop_sets_destroyed_flag(self, tk_root):
        """stop() sets _destroyed so _safe_after callbacks fired by the
        worker thread are silently discarded."""
        ticker = _make_ticker(tk_root)
        with patch.object(ticker, "_fetch_rates_bg", return_value=None):
            ticker.start()
            ticker.stop()
        assert ticker._destroyed is True
        ticker.destroy()


# ---------------------------------------------------------------------------
# 6. _read_from_cache() with a mock CacheDB
# ---------------------------------------------------------------------------

class TestRateTickerReadFromCache:
    """_read_from_cache() delegates correctly to cache_db.get_rate()."""

    def test_returns_none_when_cache_is_none(self, tk_root):
        ticker = _make_ticker(tk_root, cache_db=None)
        result = ticker._read_from_cache()
        assert result is None
        ticker.destroy()

    def test_returns_rate_when_cache_hits(self, tk_root):
        mock_cache = MagicMock()
        rate_data = {
            "usd_buying": Decimal("34.2100"),
            "usd_selling": Decimal("34.5200"),
        }
        mock_cache.get_rate.return_value = rate_data
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        result = ticker._read_from_cache()
        assert result == rate_data
        ticker.destroy()

    def test_returns_none_when_all_values_none(self, tk_root):
        """Step-back loop exhausts 6 attempts, all returning all-None dicts."""
        mock_cache = MagicMock()
        mock_cache.get_rate.return_value = {
            "usd_buying": None, "usd_selling": None,
            "eur_buying": None, "eur_selling": None,
        }
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        result = ticker._read_from_cache()
        assert result is None
        # Must have tried 6 dates
        assert mock_cache.get_rate.call_count == 6
        ticker.destroy()

    def test_returns_none_when_cache_returns_none(self, tk_root):
        """Step-back loop exhausts 6 attempts, all returning None."""
        mock_cache = MagicMock()
        mock_cache.get_rate.return_value = None
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        result = ticker._read_from_cache()
        assert result is None
        assert mock_cache.get_rate.call_count == 6
        ticker.destroy()


# ---------------------------------------------------------------------------
# 7. Placeholder shape + contrast (findings #1, #2)
# ---------------------------------------------------------------------------

def _contrast(fg: str, bg: str) -> float:
    """WCAG relative-contrast ratio between two #RRGGBB colors."""
    def _lum(hex_c: str) -> float:
        hex_c = hex_c.lstrip("#")
        chan = [int(hex_c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in chan
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    la, lb = _lum(fg), _lum(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class TestRateTickerPlaceholder:
    """Empty/unavailable state placeholder shape + readability."""

    def test_placeholder_has_no_slash(self, tk_root):
        """Finding #2: placeholder must not imply two slash-separated values."""
        from gui.panels.rate_ticker import PLACEHOLDER
        assert "/" not in PLACEHOLDER
        ticker = _make_ticker(tk_root)
        assert "/" not in ticker.lbl_usd_buy.cget("text")
        assert "/" not in ticker.lbl_eur_sell.cget("text")
        ticker.destroy()

    def test_placeholder_matches_live_single_value_shape(self, tk_root):
        """Placeholder has 4 trailing dashes to mirror the 4dp live value."""
        from gui.panels.rate_ticker import PLACEHOLDER
        # "--.----" -> one group before the dot, four after (4 decimals).
        assert PLACEHOLDER == "--.----"

    def test_format_single_none_uses_placeholder_token(self, tk_root):
        """The unavailable token equals the init placeholder (unified)."""
        from gui.panels.rate_ticker import PLACEHOLDER
        ticker = _make_ticker(tk_root)
        text, _ = ticker._format_single(None, "usd_buying")
        assert text == PLACEHOLDER
        ticker.destroy()

    def test_placeholder_color_readable_on_navy_header_dark(self, tk_root):
        """Finding #1: placeholder color must clear WCAG AA (>=4.5:1) on the
        dark navy header it actually sits on."""
        from gui.panels.rate_ticker import PLACEHOLDER_COLOR
        # header_bg dark = #1A365D
        assert _contrast(PLACEHOLDER_COLOR, "#1A365D") >= 4.5

    def test_placeholder_color_readable_on_navy_header_light(self, tk_root):
        """The ticker sits on the navy header in LIGHT mode too, where
        ticker_muted collapsed to ~1.5:1. The placeholder must stay readable."""
        from gui.panels.rate_ticker import PLACEHOLDER_COLOR
        # header_bg light = #2D4A7A
        assert _contrast(PLACEHOLDER_COLOR, "#2D4A7A") >= 4.5

    def test_initial_placeholder_color_applied(self, tk_root):
        """Rate labels start with the bright placeholder color, not the dim
        ticker_label."""
        from gui.panels.rate_ticker import PLACEHOLDER_COLOR
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        expected = get_theme().get("ticker_placeholder", PLACEHOLDER_COLOR)
        assert ticker.lbl_usd_buy.cget("text_color") == expected
        assert ticker.lbl_eur_sell.cget("text_color") == expected
        ticker.destroy()


# ---------------------------------------------------------------------------
# 8. Connection indicator gating (finding #3)
# ---------------------------------------------------------------------------

class TestRateTickerIndicatorGating:
    """The '● LIVE' badge only appears after real data has painted."""

    def test_indicator_starts_connecting_not_live(self, tk_root):
        """At construction the badge is NOT '● LIVE' and first paint is unset."""
        ticker = _make_ticker(tk_root)
        assert ticker._first_paint_done is False
        # tr() falls back to the key string until wave-2 fills the catalog.
        assert ticker.lbl_live.cget("text") != "● LIVE"
        ticker.destroy()

    def test_first_paint_flips_indicator_to_live(self, tk_root):
        """A successful _update_display sets _first_paint_done and shows LIVE."""
        ticker = _make_ticker(tk_root)
        ticker._update_display({"usd_buying": Decimal("34.2100")})
        assert ticker._first_paint_done is True
        # tr('ticker.live') falls back to the key until wave-2; assert the call
        # routed through tr by checking it is the live key/text, not connecting.
        from core.i18n import tr
        assert ticker.lbl_live.cget("text") == tr("ticker.live")
        ticker.destroy()

    def test_show_offline_only_before_first_paint(self, tk_root):
        """_show_offline must no-op once data has painted."""
        from core.i18n import tr
        ticker = _make_ticker(tk_root)
        ticker._first_paint_done = True
        before = ticker.lbl_live.cget("text")
        ticker._show_offline()
        # Unchanged because data already painted.
        assert ticker.lbl_live.cget("text") == before
        ticker.destroy()

        ticker2 = _make_ticker(tk_root)
        ticker2._show_offline()
        assert ticker2.lbl_live.cget("text") == tr("ticker.offline")
        ticker2.destroy()

    def test_fetch_bg_shows_offline_when_no_data_ever(self, tk_root):
        """With no cache and no API, the first failed fetch surfaces offline."""
        ticker = _make_ticker(tk_root, cache_db=None)
        calls = []
        # Capture _safe_after so we can run the scheduled callback synchronously.
        with patch.object(
            ticker, "_safe_after",
            side_effect=lambda ms, fn, *a: calls.append((fn, a)),
        ), patch.object(ticker, "_fetch_today_from_api", return_value=None):
            ticker._fetch_rates_bg()
        # Exactly one callback scheduled: _show_offline.
        assert len(calls) == 1
        assert calls[0][0] == ticker._show_offline
        ticker.destroy()

    def test_fetch_bg_no_offline_after_first_paint(self, tk_root):
        """Once data has painted, a later empty fetch must not show offline."""
        ticker = _make_ticker(tk_root, cache_db=None)
        ticker._first_paint_done = True
        calls = []
        with patch.object(
            ticker, "_safe_after",
            side_effect=lambda ms, fn, *a: calls.append((fn, a)),
        ), patch.object(ticker, "_fetch_today_from_api", return_value=None):
            ticker._fetch_rates_bg()
        assert calls == []
        ticker.destroy()

    def test_api_timeout_reduced(self, tk_root):
        """Finding #3: per-call timeout lowered to cap first-paint latency.

        Stub httpx + token so no real network call happens; assert the two
        sequential currency requests each use a <=5s timeout.
        """
        ticker = _make_ticker(tk_root)
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"result": {"data": {"data_detail": []}}}
        with patch("core.secure_tokens.get_token", return_value="tok"), \
                patch("httpx.get", return_value=fake_resp) as mock_get:
            ticker._fetch_today_from_api()
        assert mock_get.call_count == 2  # USD + EUR
        for call in mock_get.call_args_list:
            assert call.kwargs.get("timeout", 999) <= 5.0
        ticker.destroy()


# ---------------------------------------------------------------------------
# 9. Sparkline (finding #4)
# ---------------------------------------------------------------------------

class TestRateTickerSparkline:
    """_sparkline() + _update_sparklines() render from cache only."""

    def test_sparkline_widgets_exist(self, tk_root):
        import customtkinter as ctk
        ticker = _make_ticker(tk_root)
        assert isinstance(ticker.lbl_usd_spark, ctk.CTkLabel)
        assert isinstance(ticker.lbl_eur_spark, ctk.CTkLabel)
        # Empty until data paints.
        assert ticker.lbl_usd_spark.cget("text") == ""
        ticker.destroy()

    def test_sparkline_empty_for_short_series(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker._sparkline([]) == ""
        assert ticker._sparkline([Decimal("1.0")]) == ""
        ticker.destroy()

    def test_sparkline_maps_ascending_series(self, tk_root):
        ticker = _make_ticker(tk_root)
        bars = ticker._sparkline(
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
        )
        assert len(bars) == 4
        # Ascending: first is the lowest block, last is the highest.
        assert bars[0] == "▁"
        assert bars[-1] == "█"
        ticker.destroy()

    def test_sparkline_flat_series_is_baseline(self, tk_root):
        """A flat series renders a constant mid-level bar (no div-by-zero)."""
        ticker = _make_ticker(tk_root)
        bars = ticker._sparkline([Decimal("5"), Decimal("5"), Decimal("5")])
        assert len(bars) == 3
        assert len(set(bars)) == 1  # all identical
        ticker.destroy()

    def test_update_sparklines_reads_cache_bulk(self, tk_root):
        """_update_sparklines pulls from get_rates_bulk and renders bars."""
        from datetime import date, timedelta
        mock_cache = MagicMock()
        today = date.today()
        bulk = {
            today - timedelta(days=3): {
                "usd_selling": Decimal("34.0"), "eur_selling": Decimal("37.0"),
            },
            today - timedelta(days=2): {
                "usd_selling": Decimal("34.5"), "eur_selling": Decimal("37.2"),
            },
            today - timedelta(days=1): {
                "usd_selling": Decimal("35.0"), "eur_selling": Decimal("37.1"),
            },
        }
        mock_cache.get_rates_bulk.return_value = bulk
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        ticker._update_sparklines()
        assert mock_cache.get_rates_bulk.called
        assert ticker.lbl_usd_spark.cget("text") != ""
        assert len(ticker.lbl_usd_spark.cget("text")) == 3
        ticker.destroy()

    def test_update_sparklines_noop_without_cache(self, tk_root):
        """No cache -> no crash, label stays blank."""
        ticker = _make_ticker(tk_root, cache_db=None)
        ticker._update_sparklines()  # must not raise
        assert ticker.lbl_usd_spark.cget("text") == ""
        ticker.destroy()

    def test_update_sparklines_survives_cache_error(self, tk_root):
        """A cache read error degrades gracefully (label unchanged, no raise)."""
        mock_cache = MagicMock()
        mock_cache.get_rates_bulk.side_effect = RuntimeError("db gone")
        ticker = _make_ticker(tk_root, cache_db=mock_cache)
        ticker._update_sparklines()  # must not raise
        assert ticker.lbl_usd_spark.cget("text") == ""
        ticker.destroy()
