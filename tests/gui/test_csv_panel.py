#!/usr/bin/env python3
"""
tests/gui/test_csv_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/csv_panel.py (CSVPanel).

These tests exercise:
  1. Widget tree construction — expected child widgets are created.
  2. SafePanel mixin contract — _destroyed flag lifecycle.
  3. Label text mutation — CTkLabel.configure() works post-creation.
  4. Post-destroy resilience — _safe_after no-ops after destroy().

All tests require a display; the tk_root fixture skips them on headless CI.
No real filesystem I/O or network calls are made.
"""

import pytest

pytestmark = pytest.mark.gui


class TestCSVPanelWidgetTree:
    """CSVPanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_import_label_attribute_exists(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert hasattr(panel, "_lbl_csv"), "_lbl_csv label must exist"
        panel.destroy()

    def test_export_label_attribute_exists(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert hasattr(panel, "_lbl_csv_export"), "_lbl_csv_export label must exist"
        panel.destroy()

    def test_both_labels_are_ctk_label_instances(self, tk_root):
        import customtkinter as ctk

        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert isinstance(panel._lbl_csv, ctk.CTkLabel)
        assert isinstance(panel._lbl_csv_export, ctk.CTkLabel)
        panel.destroy()


class TestCSVPanelLabelMutation:
    """CTkLabel.configure(text=...) updates state correctly post-creation."""

    def test_import_label_accepts_text_update(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel._lbl_csv.configure(text="Test import status")
        # CTkLabel stores text in cget; verify round-trip
        assert panel._lbl_csv.cget("text") == "Test import status"
        panel.destroy()

    def test_export_label_accepts_text_update(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel._lbl_csv_export.configure(text="Test export status")
        assert panel._lbl_csv_export.cget("text") == "Test export status"
        panel.destroy()

    def test_label_starts_empty(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel._lbl_csv.cget("text") == ""
        assert panel._lbl_csv_export.cget("text") == ""
        panel.destroy()


class TestCSVPanelSafePanelMixin:
    """SafePanel mixin (_destroyed flag + _safe_after) behaves correctly."""

    def test_destroyed_flag_starts_false(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel._destroyed is False
        panel.destroy()

    def test_destroyed_flag_flips_on_destroy(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel.destroy()
        assert panel._destroyed is True

    def test_safe_after_noop_post_destroy(self, tk_root):
        """_safe_after must not raise after the widget has been destroyed."""
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel.destroy()

        called = []
        # This must silently do nothing — no TclError / RuntimeError
        panel._safe_after(0, lambda: called.append(1))

        assert called == [], "_safe_after must be a no-op post-destroy"
