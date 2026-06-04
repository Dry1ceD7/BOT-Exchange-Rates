#!/usr/bin/env python3
"""
tests/gui/test_control_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/control_panel.py (ControlPanel).

These tests exercise:
  1. Widget tree construction — _drop_zone, btn_process, btn_revert exist.
  2. Initial button state — btn_process disabled until set_queue() called.
  3. set_queue() with a single file — _dz_text shows filename.
  4. set_queue() with multiple files — _dz_text shows count.
  5. set_queue() enables btn_process.
  6. on_process callback fired when btn_process invoked.
  7. on_revert callback fired when btn_revert invoked.
  8. on_files_selected callback invoked after set_queue() with correct list.

All tests require a display; the tk_root fixture skips them on headless CI.
No real filesystem I/O or network calls are made.
"""

import pytest

pytestmark = pytest.mark.gui


class TestControlPanelWidgetTree:
    """ControlPanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_drop_zone_attribute_exists(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert hasattr(panel, "_drop_zone"), "_drop_zone frame must exist"
        panel.destroy()

    def test_btn_process_attribute_exists(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert hasattr(panel, "btn_process"), "btn_process must exist"
        panel.destroy()

    def test_btn_revert_attribute_exists(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert hasattr(panel, "btn_revert"), "btn_revert must exist"
        panel.destroy()

    def test_dz_text_attribute_exists(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert hasattr(panel, "_dz_text"), "_dz_text label must exist"
        panel.destroy()

    def test_widgets_are_correct_ctk_types(self, tk_root):
        import customtkinter as ctk

        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert isinstance(panel._drop_zone, ctk.CTkFrame)
        assert isinstance(panel.btn_process, ctk.CTkButton)
        assert isinstance(panel.btn_revert, ctk.CTkButton)
        assert isinstance(panel._dz_text, ctk.CTkLabel)
        panel.destroy()


class TestControlPanelButtonState:
    """btn_process starts disabled; set_queue() enables it."""

    def test_btn_process_starts_disabled(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert panel.btn_process.cget("state") == "disabled"
        panel.destroy()

    def test_btn_process_enabled_after_set_queue(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        panel.set_queue(["/some/path/ledger.xlsx"])
        assert panel.btn_process.cget("state") == "normal"
        panel.destroy()

    def test_file_queue_empty_at_start(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        assert panel.file_queue == []
        panel.destroy()


class TestControlPanelSetQueue:
    """set_queue() updates _dz_text and file_queue correctly."""

    def test_single_file_shows_filename(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        panel.set_queue(["/some/path/ledger_jan.xlsx"])
        assert panel._dz_text.cget("text") == "ledger_jan.xlsx"
        panel.destroy()

    def test_multiple_files_shows_count(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        files = ["/a/one.xlsx", "/b/two.xlsx", "/c/three.xlsx"]
        panel.set_queue(files)
        assert panel._dz_text.cget("text") == "3 ledgers loaded"
        panel.destroy()

    def test_two_files_shows_count(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root)
        panel.set_queue(["/a/one.xlsx", "/b/two.xlsx"])
        assert panel._dz_text.cget("text") == "2 ledgers loaded"
        panel.destroy()

    def test_set_queue_updates_file_queue_attribute(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        files = ["/a/ledger1.xlsx", "/b/ledger2.xlsx"]
        panel = ControlPanel(tk_root)
        panel.set_queue(files)
        assert panel.file_queue == files
        panel.destroy()


class TestControlPanelCallbacks:
    """Callback wiring: on_process, on_revert, on_files_selected fire correctly."""

    def test_on_process_callback_invoked_when_button_clicked(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        called = []
        panel = ControlPanel(tk_root, on_process=lambda: called.append(1))
        # Enable the button first
        panel.set_queue(["/some/ledger.xlsx"])
        panel.btn_process.invoke()
        assert called == [1], "on_process callback must fire on button click"
        panel.destroy()

    def test_on_revert_callback_invoked_when_button_clicked(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        called = []
        panel = ControlPanel(tk_root, on_revert=lambda: called.append(1))
        panel.btn_revert.invoke()
        assert called == [1], "on_revert callback must fire on button click"
        panel.destroy()

    def test_on_files_selected_callback_invoked_by_set_queue(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        received = []
        panel = ControlPanel(
            tk_root, on_files_selected=lambda files: received.append(files)
        )
        files = ["/a/ledger.xlsx"]
        panel.set_queue(files)
        assert received == [files], "on_files_selected must receive the file list"
        panel.destroy()

    def test_on_files_selected_receives_correct_multi_file_list(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        received = []
        panel = ControlPanel(
            tk_root, on_files_selected=lambda files: received.append(files)
        )
        files = ["/a/one.xlsx", "/b/two.xlsx"]
        panel.set_queue(files)
        assert len(received) == 1
        assert received[0] == files
        panel.destroy()

    def test_no_error_when_callbacks_are_none(self, tk_root):
        from gui.panels.control_panel import ControlPanel

        panel = ControlPanel(tk_root, on_process=None, on_revert=None, on_files_selected=None)
        panel.set_queue(["/a/ledger.xlsx"])
        # Clicking buttons with None callbacks must not raise
        panel.btn_process.invoke()
        panel.btn_revert.invoke()
        panel.destroy()
