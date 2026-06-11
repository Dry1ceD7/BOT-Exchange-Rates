#!/usr/bin/env python3
"""
tests/gui/test__base_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/_base_panel.py (SafePanel mixin).

These tests exercise:
  1. _destroyed flag — initialised False, flipped True on destroy().
  2. _after_target() — returns self by default; subclass override is respected.
  3. _safe_after() — no-ops when _destroyed is True.
  4. _safe_after() — swallows RuntimeError / TclError; sets _destroyed.
  5. _safe_after() — callback executes normally while widget is alive.
  6. _safe_after() — callback does NOT execute after destroy().

All tests require a display; the tk_root fixture skips them on headless CI.
"""

import contextlib
import tkinter
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

pytestmark = pytest.mark.gui


def _make_panel(tk_root):
    """Return a live SafePanel+CTkFrame instance parented to tk_root."""
    from gui.panels._base_panel import SafePanel

    class ConcretePanel(SafePanel, ctk.CTkFrame):
        pass

    return ConcretePanel(tk_root)


# ---------------------------------------------------------------------------
# Tests: _destroyed flag lifecycle
# ---------------------------------------------------------------------------

class TestDestroyedFlag:
    """_destroyed flag initialises False and flips True on destroy()."""

    def test_destroyed_initialised_false(self, tk_root):
        panel = _make_panel(tk_root)
        assert panel._destroyed is False
        panel.destroy()

    def test_destroyed_flips_true_on_destroy(self, tk_root):
        panel = _make_panel(tk_root)
        panel.destroy()
        assert panel._destroyed is True


# ---------------------------------------------------------------------------
# Tests: _after_target()
# ---------------------------------------------------------------------------

class TestAfterTarget:
    """_after_target() returns self by default; subclass override is used."""

    def test_default_after_target_returns_self(self, tk_root):
        panel = _make_panel(tk_root)
        assert panel._after_target() is panel
        panel.destroy()

    def test_subclass_override_is_respected(self, tk_root):
        """_safe_after() must call after() on whatever _after_target() returns."""
        from gui.panels._base_panel import SafePanel

        sentinel = MagicMock()

        class PanelWithOverride(SafePanel, ctk.CTkFrame):
            def _after_target(self):
                return sentinel

        panel = PanelWithOverride(tk_root)
        called = []
        panel._safe_after(0, lambda: called.append(1))
        # The mock's after() was called — that proves the override was respected.
        sentinel.after.assert_called_once()
        panel.destroy()


# ---------------------------------------------------------------------------
# Tests: _safe_after() no-op when destroyed
# ---------------------------------------------------------------------------

class TestSafeAfterNoop:
    """_safe_after() silently does nothing when _destroyed is True."""

    def test_noop_when_destroyed(self, tk_root):
        panel = _make_panel(tk_root)
        panel.destroy()
        called = []
        panel._safe_after(0, lambda: called.append(1))
        assert called == [], "_safe_after must be a no-op after destroy()"

    def test_noop_when_destroyed_flag_manually_set(self, tk_root):
        """Direct flag manipulation triggers no-op without calling destroy()."""
        panel = _make_panel(tk_root)
        panel._destroyed = True
        called = []
        panel._safe_after(0, lambda: called.append(1))
        assert called == []
        # Clean up without calling destroy() again to avoid double-teardown.
        panel._destroyed = False
        panel.destroy()


# ---------------------------------------------------------------------------
# Tests: _safe_after() exception swallowing
# ---------------------------------------------------------------------------

class TestSafeAfterExceptionHandling:
    """_safe_after() catches RuntimeError and TclError; marks _destroyed."""

    def test_runtime_error_swallowed_and_destroyed_set(self, tk_root):
        from gui.panels._base_panel import SafePanel

        class BrokenTarget(SafePanel, ctk.CTkFrame):
            def _after_target(self):
                raise RuntimeError("widget gone")

        panel = BrokenTarget(tk_root)
        # Must not raise; _destroyed must become True.
        panel._safe_after(0, lambda: None)
        assert panel._destroyed is True
        # Widget is already effectively dead; suppress any teardown error.
        with contextlib.suppress(Exception):
            panel.destroy()

    def test_tcl_error_swallowed_and_destroyed_set(self, tk_root):
        from gui.panels._base_panel import SafePanel

        class TclBrokenTarget(SafePanel, ctk.CTkFrame):
            def _after_target(self):
                raise tkinter.TclError("application has been destroyed")

        panel = TclBrokenTarget(tk_root)
        panel._safe_after(0, lambda: None)
        assert panel._destroyed is True
        with contextlib.suppress(Exception):
            panel.destroy()


# ---------------------------------------------------------------------------
# Tests: _safe_after() callback execution
# ---------------------------------------------------------------------------

class TestSafeAfterCallback:
    """Callback scheduled via _safe_after() executes when widget is alive."""

    def test_callback_executes_when_alive(self, tk_root):
        """after(0, cb) fires during the next event-loop cycle.

        We pump the event loop via tk_root.update() to drain pending callbacks.
        """
        panel = _make_panel(tk_root)
        called = []
        panel._safe_after(0, lambda: called.append(1))
        tk_root.update()  # drain pending after() callbacks
        assert called == [1], "callback must execute while panel is alive"
        panel.destroy()

    def test_callback_receives_args(self, tk_root):
        panel = _make_panel(tk_root)
        called = []
        panel._safe_after(0, lambda a, b: called.append((a, b)), "x", 2)
        tk_root.update()
        assert called == [("x", 2)]
        panel.destroy()


# ---------------------------------------------------------------------------
# Tests: destroy() cancels pending after() callbacks (F137)
# ---------------------------------------------------------------------------

class TestDestroyCancelsPendingAfters:
    """destroy() must after_cancel every still-pending _safe_after timer."""

    def test_destroy_cancels_pending_timer(self, tk_root):
        panel = _make_panel(tk_root)
        called = []
        panel._safe_after(60000, lambda: called.append(1))
        assert len(panel._pending_after_ids) == 1
        after_id = panel._pending_after_ids[0]
        # The timer is registered with the Tcl interpreter...
        assert after_id in str(tk_root.tk.call("after", "info"))
        panel.destroy()
        # ...and gone once destroy() cancelled it.
        assert after_id not in str(tk_root.tk.call("after", "info"))
        assert panel._pending_after_ids == []
        assert called == []

    def test_fired_callback_drops_tracking_entry(self, tk_root):
        """Fired callbacks remove their id so the list stays bounded."""
        panel = _make_panel(tk_root)
        called = []
        panel._safe_after(0, lambda: called.append(1))
        assert len(panel._pending_after_ids) == 1
        tk_root.update()
        assert called == [1]
        assert panel._pending_after_ids == []
        panel.destroy()

    def test_destroy_survives_stale_after_id(self, tk_root):
        """after_cancel on an unknown/stale id raises TclError — suppressed."""
        panel = _make_panel(tk_root)
        panel._pending_after_ids.append("after#999999999")
        panel.destroy()  # must not raise
        assert panel._destroyed is True
        assert panel._pending_after_ids == []
