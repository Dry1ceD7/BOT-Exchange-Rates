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
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_usd_buy.cget("text") == "BUY --/--"
        ticker.destroy()

    def test_initial_usd_sell_text(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_usd_sell.cget("text") == "SELL --/--"
        ticker.destroy()

    def test_initial_eur_buy_text(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_eur_buy.cget("text") == "BUY --/--"
        ticker.destroy()

    def test_initial_eur_sell_text(self, tk_root):
        ticker = _make_ticker(tk_root)
        assert ticker.lbl_eur_sell.cget("text") == "SELL --/--"
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

    def test_apply_theme_dark_palette(self, tk_root):
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        theme = get_theme()
        ticker.apply_theme(theme)  # must not raise
        assert ticker.lbl_usd_title.cget("text_color") == theme["ticker_value"]
        assert ticker.lbl_eur_title.cget("text_color") == theme["ticker_value"]
        assert ticker.lbl_usd_buy.cget("text_color") == theme["ticker_label"]
        assert ticker.lbl_usd_sell.cget("text_color") == theme["ticker_label"]
        assert ticker.lbl_eur_buy.cget("text_color") == theme["ticker_label"]
        assert ticker.lbl_eur_sell.cget("text_color") == theme["ticker_label"]
        assert ticker.lbl_live.cget("text_color") == theme["ticker_live"]
        assert ticker.lbl_time.cget("text_color") == theme["ticker_label"]
        ticker.destroy()

    def test_apply_theme_custom_dict(self, tk_root):
        """apply_theme() uses dict.get() with defaults — custom dict is safe."""
        ticker = _make_ticker(tk_root)
        custom = {
            "ticker_value": "#AABBCC",
            "ticker_label": "#DDEEFF",
            "ticker_live":  "#FF0000",
        }
        ticker.apply_theme(custom)
        assert ticker.lbl_usd_title.cget("text_color") == "#AABBCC"
        assert ticker.lbl_usd_buy.cget("text_color") == "#DDEEFF"
        assert ticker.lbl_live.cget("text_color") == "#FF0000"
        ticker.destroy()

    def test_apply_theme_empty_dict_uses_defaults(self, tk_root):
        """apply_theme() falls back to hardcoded defaults for missing keys."""
        ticker = _make_ticker(tk_root)
        ticker.apply_theme({})  # all keys missing — should not raise
        assert ticker.lbl_usd_title.cget("text_color") == "#FFFFFF"
        assert ticker.lbl_usd_buy.cget("text_color") == "#94A3B8"
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

    def test_none_rate_returns_muted(self, tk_root):
        from gui.theme import get_theme
        ticker = _make_ticker(tk_root)
        text, color = ticker._format_single(None, "usd_buying")
        assert text == "--.--"
        assert color == get_theme()["ticker_muted"]
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
