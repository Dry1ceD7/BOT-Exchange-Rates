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


class TestLiveConsolePanelLineCap:
    """_poll caps the Text widget to MAX_LINES to bound memory growth."""

    def test_poll_caps_to_max_lines(self, tk_root):
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        # Shrink the cap so the test stays fast.
        panel.MAX_LINES = 50
        # Push far more events than the cap through the real drain path.
        for i in range(200):
            panel.event_bus.push({"type": "log", "msg": f"line {i}"})
        panel._polling = True
        # Drive a single drain cycle directly (no after() loop needed).
        panel._poll()
        panel._polling = False

        tb = panel._textbox._textbox
        line_count = int(tb.index("end-1c").split(".")[0])
        assert line_count <= panel.MAX_LINES, (
            f"expected <= {panel.MAX_LINES} lines, got {line_count}"
        )
        panel.destroy()

    def test_poll_keeps_newest_lines(self, tk_root):
        """When trimmed, the most recent lines survive and oldest are dropped."""
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.MAX_LINES = 10
        for i in range(60):
            panel.event_bus.push({"type": "log", "msg": f"line {i}"})
        panel._polling = True
        panel._poll()
        panel._polling = False

        content = panel._textbox.get("1.0", "end")
        assert "line 59" in content, "newest line must be retained"
        assert "line 0" not in content, "oldest line must be trimmed"
        panel.destroy()

    def test_trim_lines_noop_below_cap(self, tk_root):
        """_trim_lines leaves content untouched when under the cap."""
        from gui.panels.live_console import LiveConsolePanel

        panel = LiveConsolePanel(tk_root)
        panel.MAX_LINES = 2000
        panel.append_line("only one line")
        tb = panel._textbox._textbox
        panel._textbox.configure(state="normal")
        panel._trim_lines(tb)
        panel._textbox.configure(state="disabled")
        content = panel._textbox.get("1.0", "end")
        assert "only one line" in content
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
