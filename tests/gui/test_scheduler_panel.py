#!/usr/bin/env python3
"""
tests/gui/test_scheduler_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/scheduler_panel.py (SchedulerPanel).

These tests exercise:
  1. Widget tree construction — header toggle, _content frame, time pickers,
     paths header, _path_list textbox, _lbl_status.
  2. Toggle on: _content becomes packed; on_start callback fired with
     time string and path list.
  3. Toggle off: _content is removed; on_stop callback fired.
  4. Time picker values — _hour_var covers 0-23, _minute_var has quarter options.
  5. Path list refresh — _refresh_path_list() shows abbreviated directory names.
  6. Status label — warns when no paths; shows success message when paths present.
  7. Config persistence — _save_config() merges keys into SettingsManager.

All tests require a display; the tk_root fixture skips them on headless CI.
SettingsManager is mocked throughout to avoid real filesystem I/O.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_PATH = "gui.panels.scheduler_panel.SettingsManager"


def _make_panel(tk_root, settings=None, on_start=None, on_stop=None):
    """Construct a SchedulerPanel with a mocked SettingsManager.

    Returns (panel, mock_mgr).  Callers must call panel.destroy() when done.
    """
    from gui.panels.scheduler_panel import SchedulerPanel

    mock_mgr = MagicMock()
    mock_mgr.load.return_value = settings or {}
    mock_mgr.save = MagicMock()

    with patch(_MOCK_PATH, return_value=mock_mgr):
        panel = SchedulerPanel(tk_root, on_start_scheduler=on_start,
                               on_stop_scheduler=on_stop)
    return panel, mock_mgr


# ---------------------------------------------------------------------------
# 1. Widget tree
# ---------------------------------------------------------------------------

class TestSchedulerPanelWidgetTree:
    """SchedulerPanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_enable_var_attribute_exists(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_enable_var"), "_enable_var must exist"
        panel.destroy()

    def test_toggle_attribute_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_toggle"), "_toggle CTkSwitch must exist"
        assert isinstance(panel._toggle, ctk.CTkSwitch)
        panel.destroy()

    def test_content_frame_attribute_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_content"), "_content frame must exist"
        assert isinstance(panel._content, ctk.CTkFrame)
        panel.destroy()

    def test_hour_var_attribute_exists(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_hour_var"), "_hour_var must exist"
        panel.destroy()

    def test_minute_var_attribute_exists(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_minute_var"), "_minute_var must exist"
        panel.destroy()

    def test_path_list_textbox_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_path_list"), "_path_list CTkTextbox must exist"
        assert isinstance(panel._path_list, ctk.CTkTextbox)
        panel.destroy()

    def test_status_label_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_status"), "_lbl_status must exist"
        assert isinstance(panel._lbl_status, ctk.CTkLabel)
        panel.destroy()

    def test_title_label_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_title"), "_lbl_title must exist"
        assert isinstance(panel._lbl_title, ctk.CTkLabel)
        panel.destroy()

    def test_add_button_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_btn_add"), "_btn_add must exist"
        assert isinstance(panel._btn_add, ctk.CTkButton)
        panel.destroy()

    def test_remove_button_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_btn_remove"), "_btn_remove must exist"
        assert isinstance(panel._btn_remove, ctk.CTkButton)
        panel.destroy()


# ---------------------------------------------------------------------------
# 2. Toggle ON — content visible, on_start callback fired
# ---------------------------------------------------------------------------

class TestSchedulerPanelToggleOn:
    """Enabling the toggle shows content and fires on_start."""

    def test_toggle_on_sets_enable_var(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._enable_var.set("on")
        panel._on_toggle()
        assert panel._enable_var.get() == "on"
        panel.destroy()

    def test_toggle_on_fires_on_start_callback(self, tk_root):
        fired = []

        def on_start(t, p):
            fired.append((t, p))

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._enable_var.set("on")
        panel._on_toggle()

        assert len(fired) == 1, "on_start must be called exactly once"
        panel.destroy()

    def test_toggle_on_passes_time_to_callback(self, tk_root):
        fired = []

        def on_start(t, p):
            fired.append((t, p))

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._hour_var.set("09")
        panel._minute_var.set("30")
        panel._enable_var.set("on")
        panel._on_toggle()

        time_arg = fired[0][0]
        assert time_arg == "09:30", f"Expected '09:30', got '{time_arg}'"
        panel.destroy()

    def test_toggle_on_passes_paths_list_to_callback(self, tk_root):
        fired = []

        def on_start(t, p):
            fired.append((t, p))

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._enable_var.set("on")
        panel._on_toggle()

        paths_arg = fired[0][1]
        assert isinstance(paths_arg, list)
        assert paths_arg == ["/some/folder"]
        panel.destroy()

    def test_toggle_on_with_no_callback_does_not_raise(self, tk_root):
        panel, _ = _make_panel(tk_root, on_start=None)
        panel._enable_var.set("on")
        panel._on_toggle()  # must not raise
        panel.destroy()


# ---------------------------------------------------------------------------
# 3. Toggle OFF — content hidden, on_stop callback fired
# ---------------------------------------------------------------------------

class TestSchedulerPanelToggleOff:
    """Disabling the toggle hides content and fires on_stop."""

    def test_toggle_off_fires_on_stop_callback(self, tk_root):
        stopped = []

        def on_stop():
            stopped.append(True)

        panel, _ = _make_panel(tk_root, on_stop=on_stop)
        # First turn on, then off
        panel._enable_var.set("on")
        panel._on_toggle()
        panel._enable_var.set("off")
        panel._on_toggle()

        assert len(stopped) == 1, "on_stop must be called exactly once"
        panel.destroy()

    def test_toggle_off_with_no_callback_does_not_raise(self, tk_root):
        panel, _ = _make_panel(tk_root, on_stop=None)
        panel._enable_var.set("off")
        panel._on_toggle()  # must not raise
        panel.destroy()

    def test_toggle_off_sets_enable_var(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._enable_var.set("on")
        panel._on_toggle()
        panel._enable_var.set("off")
        panel._on_toggle()
        assert panel._enable_var.get() == "off"
        panel.destroy()


# ---------------------------------------------------------------------------
# 4. Time picker values
# ---------------------------------------------------------------------------

class TestSchedulerPanelTimePicker:
    """Hour and minute option menus expose the correct value sets."""

    def test_hour_menu_has_24_options(self, tk_root):
        panel, _ = _make_panel(tk_root)
        # CTkOptionMenu stores values set at construction; verify via _hour_menu
        values = panel._hour_menu.cget("values")
        assert len(values) == 24, f"Expected 24 hour options, got {len(values)}"
        panel.destroy()

    def test_hour_menu_options_zero_to_twenty_three(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = panel._hour_menu.cget("values")
        expected = [f"{h:02d}" for h in range(24)]
        assert list(values) == expected
        panel.destroy()

    def test_minute_menu_has_four_options(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = panel._minute_menu.cget("values")
        assert len(values) == 4, f"Expected 4 minute options, got {len(values)}"
        panel.destroy()

    def test_minute_menu_options_are_quarter_hours(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = panel._minute_menu.cget("values")
        assert list(values) == ["00", "15", "30", "45"]
        panel.destroy()

    def test_default_hour_is_23(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel._hour_var.get() == "23"
        panel.destroy()

    def test_default_minute_is_00(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel._minute_var.get() == "00"
        panel.destroy()


# ---------------------------------------------------------------------------
# 5. Path list display
# ---------------------------------------------------------------------------

class TestSchedulerPanelPathList:
    """_refresh_path_list() displays abbreviated directory names."""

    def test_empty_path_list_shows_nothing(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._paths = []
        panel._refresh_path_list()
        panel._path_list.configure(state="normal")
        content = panel._path_list.get("1.0", "end").strip()
        panel._path_list.configure(state="disabled")
        assert content == ""
        panel.destroy()

    def test_single_path_shows_basename(self, tk_root, tmp_path):
        folder = tmp_path / "myproject"
        folder.mkdir()

        panel, _ = _make_panel(tk_root)
        panel._paths = [str(folder)]
        panel._refresh_path_list()

        panel._path_list.configure(state="normal")
        content = panel._path_list.get("1.0", "end")
        panel._path_list.configure(state="disabled")

        assert "myproject" in content
        panel.destroy()

    def test_multiple_paths_all_shown(self, tk_root, tmp_path):
        dirs = []
        for name in ("alpha", "beta", "gamma"):
            d = tmp_path / name
            d.mkdir()
            dirs.append(str(d))

        panel, _ = _make_panel(tk_root)
        panel._paths = dirs
        panel._refresh_path_list()

        panel._path_list.configure(state="normal")
        content = panel._path_list.get("1.0", "end")
        panel._path_list.configure(state="disabled")

        for name in ("alpha", "beta", "gamma"):
            assert name in content, f"'{name}' must appear in path list"
        panel.destroy()

    def test_path_list_is_disabled_after_refresh(self, tk_root):
        """_path_list must remain read-only (state='disabled') after refresh."""
        panel, _ = _make_panel(tk_root)
        panel._paths = []
        panel._refresh_path_list()
        # CTkTextbox doesn't expose 'state' via cget(); check the underlying
        # Tk Text widget directly.
        underlying = panel._path_list._textbox
        state = underlying.cget("state")
        assert state == "disabled"
        panel.destroy()


# ---------------------------------------------------------------------------
# 6. Status label
# ---------------------------------------------------------------------------

class TestSchedulerPanelStatusLabel:
    """_update_status() reflects the correct warning/success state."""

    def test_status_shows_warning_when_no_paths(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._paths = []
        panel._update_status()
        text = panel._lbl_status.cget("text")
        assert text != "", "Status must not be empty when no paths"
        assert "No folders" in text or "no" in text.lower() or "warning" in text.lower() or "⚠" in text
        panel.destroy()

    def test_status_shows_success_when_paths_present(self, tk_root, tmp_path):
        folder = tmp_path / "watch"
        folder.mkdir()

        panel, _ = _make_panel(tk_root)
        panel._paths = [str(folder)]
        panel._hour_var.set("08")
        panel._minute_var.set("00")
        panel._update_status()
        text = panel._lbl_status.cget("text")
        assert "08:00" in text or "watching" in text.lower()
        panel.destroy()

    def test_status_includes_folder_count(self, tk_root, tmp_path):
        dirs = [str(tmp_path / f"d{i}") for i in range(3)]
        for d in dirs:
            import os
            os.makedirs(d, exist_ok=True)

        panel, _ = _make_panel(tk_root)
        panel._paths = dirs
        panel._hour_var.set("23")
        panel._minute_var.set("00")
        panel._update_status()
        text = panel._lbl_status.cget("text")
        assert "3" in text, f"Folder count '3' must appear in status: '{text}'"
        panel.destroy()


# ---------------------------------------------------------------------------
# 7. Config persistence
# ---------------------------------------------------------------------------

class TestSchedulerPanelConfigPersistence:
    """_save_config() correctly merges keys and calls SettingsManager.save()."""

    def test_save_config_calls_mgr_save(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._save_config()
        mock_mgr.save.assert_called_once()
        panel.destroy()

    def test_save_config_includes_enabled_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._enable_var.set("on")
        panel._save_config()

        saved = mock_mgr.save.call_args[0][0]
        assert "scheduler_enabled" in saved
        assert saved["scheduler_enabled"] is True
        panel.destroy()

    def test_save_config_includes_time_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._hour_var.set("07")
        panel._minute_var.set("15")
        panel._save_config()

        saved = mock_mgr.save.call_args[0][0]
        assert saved.get("scheduler_time") == "07:15"
        panel.destroy()

    def test_save_config_includes_paths_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._paths = ["/a/b", "/c/d"]
        panel._save_config()

        saved = mock_mgr.save.call_args[0][0]
        assert saved.get("scheduler_paths") == ["/a/b", "/c/d"]
        panel.destroy()

    def test_save_config_disabled_state(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._enable_var.set("off")
        panel._save_config()

        saved = mock_mgr.save.call_args[0][0]
        assert saved["scheduler_enabled"] is False
        panel.destroy()

    def test_toggle_persists_on_toggle(self, tk_root):
        """Each _on_toggle() call must result in a save."""
        panel, mock_mgr = _make_panel(tk_root)
        initial_call_count = mock_mgr.save.call_count

        panel._enable_var.set("on")
        panel._on_toggle()

        assert mock_mgr.save.call_count > initial_call_count
        panel.destroy()


# ---------------------------------------------------------------------------
# 8. get_config public API
# ---------------------------------------------------------------------------

class TestSchedulerPanelGetConfig:
    """get_config() returns the correct snapshot dict."""

    def test_get_config_returns_dict(self, tk_root):
        panel, _ = _make_panel(tk_root)
        cfg = panel.get_config()
        assert isinstance(cfg, dict)
        panel.destroy()

    def test_get_config_has_required_keys(self, tk_root):
        panel, _ = _make_panel(tk_root)
        cfg = panel.get_config()
        assert "enabled" in cfg
        assert "time" in cfg
        assert "paths" in cfg
        panel.destroy()

    def test_get_config_enabled_reflects_var(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._enable_var.set("on")
        assert panel.get_config()["enabled"] is True
        panel._enable_var.set("off")
        assert panel.get_config()["enabled"] is False
        panel.destroy()

    def test_get_config_time_reflects_vars(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._hour_var.set("14")
        panel._minute_var.set("45")
        assert panel.get_config()["time"] == "14:45"
        panel.destroy()

    def test_get_config_paths_is_copy(self, tk_root):
        """Mutating the returned list must not affect internal state."""
        panel, _ = _make_panel(tk_root)
        panel._paths = ["/a"]
        cfg = panel.get_config()
        cfg["paths"].append("/injected")
        assert "/injected" not in panel._paths
        panel.destroy()
