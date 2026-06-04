#!/usr/bin/env python3
"""
tests/gui/test_live_console.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/live_console.py (LiveConsolePanel).

These tests exercise:
  1. Widget tree construction — _textbox CTkTextbox is created.
  2. Color tag setup — _setup_color_tags() configures expected tag names.
  3. Polling flag contract — starts False, set True by start_polling().
  4. append_line / clear — text content round-trip without network/file I/O.
  5. EventBus wiring — panel owns an EventBus by default.

All tests require a display; the tk_root fixture skips them on headless CI.
"""

import pytest

pytestmark = pytest.mark.gui


class TestLiveConsolePanelWidgetTree:
    """LiveConsolePanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_textbox_attribute_exists(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        assert hasattr(panel, "_textbox"), "_textbox must be created"
        panel.destroy()

    def test_textbox_is_ctk_textbox(self, tk_root):
        import customtkinter as ctk

        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        assert isinstance(panel._textbox, ctk.CTkTextbox)
        panel.destroy()


class TestLiveConsolePanelColorTags:
    """_setup_color_tags() registers the expected Tk text tags."""

    def test_color_tags_registered(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        tb = panel._textbox._textbox  # underlying Tk Text widget
        configured_tags = tb.tag_names()
        for tag in ("error", "success", "accent", "log", "warning"):
            assert tag in configured_tags, f"tag '{tag}' must be configured"
        panel.destroy()

    def test_error_tag_has_foreground(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        tb = panel._textbox._textbox
        fg = tb.tag_cget("error", "foreground")
        assert fg, "error tag must have a foreground color"
        panel.destroy()


class TestLiveConsolePanelPolling:
    """Polling flag initializes correctly and toggles via start/stop."""

    def test_polling_flag_starts_false(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        assert panel._polling is False
        panel.destroy()

    def test_start_polling_sets_flag_true(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.start_polling()
        assert panel._polling is True
        # Stop polling to prevent after() callbacks firing after destroy
        panel.stop_polling()
        panel.destroy()

    def test_stop_polling_clears_flag(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.start_polling()
        panel.stop_polling()
        assert panel._polling is False
        panel.destroy()


class TestLiveConsolePanelContent:
    """append_line and clear manipulate the textbox content correctly."""

    def test_append_line_adds_content(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.append_line("hello world")
        content = panel._textbox.get("1.0", "end")
        assert "hello world" in content
        panel.destroy()

    def test_append_line_with_tag(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.append_line("error occurred", tag="error")
        content = panel._textbox.get("1.0", "end")
        assert "error occurred" in content
        panel.destroy()

    def test_clear_empties_textbox(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.append_line("line to clear")
        panel.clear()
        content = panel._textbox.get("1.0", "end").strip()
        assert content == "", "clear() must empty the textbox"
        panel.destroy()


class TestLiveConsolePanelEventBus:
    """Panel owns a default EventBus and exposes it via property."""

    def test_event_bus_property_returns_bus(self, tk_root):
        from core.workers.event_bus import EventBus
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        assert isinstance(panel.event_bus, EventBus)
        panel.destroy()

    def test_custom_event_bus_injected(self, tk_root):
        from core.workers.event_bus import EventBus
        from gui.panels.live_console import LiveConsolePanel

        bus = EventBus()
        panel = LiveConsolePanel(tk_root, event_bus=bus)
        assert panel.event_bus is bus
        panel.destroy()
