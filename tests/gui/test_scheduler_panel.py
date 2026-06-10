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
        # A valid folder is required for the toggle to stay on (zero-folder
        # guard). Without one the toggle snaps back off — covered separately.
        panel._paths = ["/some/folder"]
        panel._enable_var.set("on")
        panel._on_toggle()
        assert panel._enable_var.get() == "on"
        panel.destroy()

    def test_toggle_on_fires_on_start_callback(self, tk_root):
        fired = []

        def on_start(t, p, **kwargs):
            fired.append((t, p))

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._enable_var.set("on")
        panel._on_toggle()

        assert len(fired) == 1, "on_start must be called exactly once"
        panel.destroy()

    def test_toggle_on_passes_time_to_callback(self, tk_root):
        fired = []

        def on_start(t, p, **kwargs):
            fired.append((t, p))

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._hour_var.set("09")
        panel._minute_var.set("30")
        panel._enable_var.set("on")
        panel._on_toggle()

        time_arg = fired[0][0]
        assert time_arg == "09:30", f"Expected '09:30', got '{time_arg}'"
        panel.destroy()

    def test_toggle_on_passes_paths_list_to_callback(self, tk_root):
        fired = []

        def on_start(t, p, **kwargs):
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
        panel._paths = ["/some/folder"]
        panel._enable_var.set("on")
        panel._on_toggle()  # must not raise
        panel.destroy()

    def test_toggle_on_with_zero_folders_reverts_to_off(self, tk_root):
        """Fix: flipping the toggle ON with no watch folders must refuse to
        arm — the switch snaps back off, on_start is never called, and the
        persisted enabled flag stays False so the scheduler is never advertised
        as running over an empty path list.
        """
        fired = []

        panel, mock_mgr = _make_panel(
            tk_root, on_start=lambda t, p, **kw: fired.append((t, p))
        )
        panel._paths = []
        mock_mgr.set.reset_mock()
        panel._enable_var.set("on")
        panel._on_toggle()

        assert panel._enable_var.get() == "off"
        assert fired == [], "on_start must NOT fire without a watch folder"
        saved = {c.args[0]: c.args[1] for c in mock_mgr.set.call_args_list}
        assert saved.get("scheduler_enabled") is False
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

    def test_minute_menu_has_sixty_options(self, tk_root):
        # Widened from the old quarter-hour-only set so any minute is
        # selectable and any persisted off-grid minute round-trips.
        panel, _ = _make_panel(tk_root)
        values = panel._minute_menu.cget("values")
        assert len(values) == 60, f"Expected 60 minute options, got {len(values)}"
        panel.destroy()

    def test_minute_menu_options_are_full_range(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = panel._minute_menu.cget("values")
        assert list(values) == [f"{m:02d}" for m in range(60)]
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

def _set_calls(mock_mgr) -> dict:
    """Collapse all mgr.set(key, value) calls into a {key: value} dict.

    _save_config now persists each scheduler_* key individually via
    SettingsManager.set() (a locked read-modify-write) instead of writing a
    stale full-settings blob, so it never clobbers keys owned by the Settings
    modal. Tests inspect the per-key set() calls rather than save().
    """
    return {c.args[0]: c.args[1] for c in mock_mgr.set.call_args_list}


class TestSchedulerPanelConfigPersistence:
    """_save_config() persists each scheduler_* key via SettingsManager.set()."""

    def test_save_config_calls_mgr_set(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        mock_mgr.set.reset_mock()
        panel._save_config()
        assert mock_mgr.set.called
        # Must NOT write a full-blob save() that could clobber modal keys.
        assert not mock_mgr.save.called
        panel.destroy()

    def test_save_config_includes_enabled_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._paths = ["/a/b"]
        panel._enable_var.set("on")
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert "scheduler_enabled" in saved
        assert saved["scheduler_enabled"] is True
        panel.destroy()

    def test_save_config_includes_time_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._hour_var.set("07")
        panel._minute_var.set("15")
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved.get("scheduler_time") == "07:15"
        panel.destroy()

    def test_save_config_includes_paths_key(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._paths = ["/a/b", "/c/d"]
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved.get("scheduler_paths") == ["/a/b", "/c/d"]
        panel.destroy()

    def test_save_config_disabled_state(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._enable_var.set("off")
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved["scheduler_enabled"] is False
        panel.destroy()

    def test_save_config_only_touches_scheduler_keys(self, tk_root):
        """Fix: _save_config must write ONLY scheduler_* keys so it can never
        clobber rate_type / appearance / anomaly_threshold owned by the
        Settings modal.
        """
        panel, mock_mgr = _make_panel(tk_root)
        mock_mgr.set.reset_mock()
        panel._save_config()

        touched = {c.args[0] for c in mock_mgr.set.call_args_list}
        assert touched == {
            "scheduler_enabled",
            "scheduler_time",
            "scheduler_paths",
            "scheduler_skip_weekends",
            "scheduler_skip_holidays",
        }
        panel.destroy()

    def test_toggle_persists_on_toggle(self, tk_root):
        """Each _on_toggle() call must result in a persisted set()."""
        panel, mock_mgr = _make_panel(tk_root)
        panel._paths = ["/a/b"]
        mock_mgr.set.reset_mock()

        panel._enable_var.set("on")
        panel._on_toggle()

        assert mock_mgr.set.called
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


# ---------------------------------------------------------------------------
# 9. Persisted-enabled scheduler ARMS on launch (findings #1 / #2)
# ---------------------------------------------------------------------------

class TestSchedulerPanelArmsOnLaunch:
    """A persisted scheduler_enabled=True with a valid folder must actually
    start the background scheduler when the panel loads — not merely restore
    the toggle visual and 'Next run' status while the AutoScheduler stays dead.
    """

    def test_persisted_enabled_with_valid_folder_arms_on_launch(
        self, tk_root, tmp_path
    ):
        folder = tmp_path / "ledgers"
        folder.mkdir()
        fired = []

        settings = {
            "scheduler_enabled": True,
            "scheduler_time": "08:30",
            "scheduler_paths": [str(folder)],
        }
        panel, _ = _make_panel(
            tk_root,
            settings=settings,
            on_start=lambda t, p, **kw: fired.append((t, p)),
        )

        assert len(fired) == 1, "on_start must fire once on persisted launch"
        time_arg, paths_arg = fired[0]
        assert time_arg == "08:30"
        assert paths_arg == [str(folder)]
        assert panel._enable_var.get() == "on"
        panel.destroy()

    def test_persisted_disabled_does_not_arm(self, tk_root, tmp_path):
        folder = tmp_path / "ledgers"
        folder.mkdir()
        fired = []

        settings = {
            "scheduler_enabled": False,
            "scheduler_time": "08:30",
            "scheduler_paths": [str(folder)],
        }
        panel, _ = _make_panel(
            tk_root,
            settings=settings,
            on_start=lambda t, p, **kw: fired.append((t, p)),
        )

        assert fired == [], "disabled scheduler must not arm on launch"
        assert panel._enable_var.get() == "off"
        panel.destroy()

    def test_persisted_enabled_no_valid_folder_repairs_to_off(
        self, tk_root, tmp_path
    ):
        """Fix: persisted-enabled but the only watch folder is now missing.
        The panel must NOT arm and must repair the on-disk flag to disabled
        rather than advertising a 'Next run' over an empty path list.
        """
        missing = tmp_path / "gone"  # never created
        fired = []

        settings = {
            "scheduler_enabled": True,
            "scheduler_time": "23:00",
            "scheduler_paths": [str(missing)],
        }
        panel, mock_mgr = _make_panel(
            tk_root,
            settings=settings,
            on_start=lambda t, p, **kw: fired.append((t, p)),
        )

        assert fired == [], "must not arm when no valid folder survives"
        assert panel._enable_var.get() == "off"
        # On-disk flag repaired to False via per-key set().
        saved = {c.args[0]: c.args[1] for c in mock_mgr.set.call_args_list}
        assert saved.get("scheduler_enabled") is False
        panel.destroy()


# ---------------------------------------------------------------------------
# 10. Minute picker — full 60-minute resolution + off-grid persisted repair
#     (finding: scheduler_panel.py:128)
# ---------------------------------------------------------------------------

class TestSchedulerPanelMinuteResolution:
    """Any minute is selectable; a persisted off-grid minute round-trips."""

    def test_minute_menu_has_sixty_options(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = panel._minute_menu.cget("values")
        assert len(values) == 60, f"Expected 60 minute options, got {len(values)}"
        panel.destroy()

    def test_minute_menu_options_zero_padded_full_range(self, tk_root):
        panel, _ = _make_panel(tk_root)
        values = list(panel._minute_menu.cget("values"))
        assert values == [f"{m:02d}" for m in range(60)]
        # The previously-impossible 08:05 minute is now offered.
        assert "05" in values
        assert "37" in values
        panel.destroy()

    def test_persisted_non_quarter_minute_round_trips(self, tk_root):
        """A persisted 08:37 must load as 37 (not snap to a quarter hour)."""
        settings = {"scheduler_time": "08:37", "scheduler_paths": []}
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._hour_var.get() == "08"
        assert panel._minute_var.get() == "37"
        # And 37 is a real option, so opening the dropdown won't reset it.
        assert "37" in panel._minute_menu.cget("values")
        panel.destroy()

    def test_persisted_unpadded_components_are_repaired(self, tk_root):
        """Legacy '7:5' must snap to valid two-digit options ('07','05')."""
        settings = {"scheduler_time": "7:5", "scheduler_paths": []}
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._hour_var.get() == "07"
        assert panel._minute_var.get() == "05"
        panel.destroy()

    def test_persisted_garbage_minute_falls_back(self, tk_root):
        """A non-numeric persisted minute falls back to a valid option."""
        settings = {"scheduler_time": "09:zz", "scheduler_paths": []}
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._hour_var.get() == "09"
        # Fallback minute "00" is a valid dropdown option.
        assert panel._minute_var.get() in panel._minute_menu.cget("values")
        assert panel._minute_var.get() == "00"
        panel.destroy()


# ---------------------------------------------------------------------------
# 11. apply_theme recolors every interior widget (finding: scheduler_panel:326)
# ---------------------------------------------------------------------------

class TestSchedulerPanelApplyTheme:
    """A theme switch must recolor the dropdowns, buttons, list and labels —
    not just the frame and title — so the panel is never half-themed.
    """

    def test_apply_theme_recolors_option_menus(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        t = get_theme()
        panel.apply_theme(t)
        for menu in (panel._hour_menu, panel._minute_menu):
            assert menu.cget("fg_color") == t["option_bg"]
            assert menu.cget("button_color") == t["trust_blue"]
        panel.destroy()

    def test_apply_theme_recolors_action_buttons(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        t = get_theme()
        panel.apply_theme(t)
        assert panel._btn_add.cget("fg_color") == t["trust_blue"]
        assert panel._btn_add.cget("hover_color") == t["blue_hover"]
        assert panel._btn_remove.cget("fg_color") == t["revert_bg"]
        assert panel._btn_remove.cget("hover_color") == t["revert_hover"]
        panel.destroy()

    def test_apply_theme_recolors_path_list(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        t = get_theme()
        panel.apply_theme(t)
        assert panel._path_list.cget("fg_color") == t["path_list_bg"]
        assert panel._path_list.cget("border_color") == t["sched_border"]
        panel.destroy()

    def test_apply_theme_recolors_static_labels(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        t = get_theme()
        panel.apply_theme(t)
        assert panel._lbl_run_at.cget("text_color") == t["text_muted"]
        assert panel._lbl_watch.cget("text_color") == t["text_muted"]
        assert panel._lbl_colon.cget("text_color") == t["text_primary"]
        panel.destroy()

    def test_apply_theme_recolors_frame_and_title(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        t = get_theme()
        panel.apply_theme(t)
        assert panel.cget("fg_color") == t["sched_bg"]
        assert panel.cget("border_color") == t["sched_border"]
        assert panel._lbl_title.cget("text_color") == t["text_primary"]
        panel.destroy()

    def test_apply_theme_does_not_raise(self, tk_root):
        from gui.theme import get_theme

        panel, _ = _make_panel(tk_root)
        panel.apply_theme(get_theme())  # must not raise
        panel.destroy()


# ---------------------------------------------------------------------------
# 12. Skip-weekends / skip-holidays checkboxes
# ---------------------------------------------------------------------------

class TestSchedulerPanelSkipToggles:
    """Skip-weekends and skip-holidays checkboxes persist, restore, and forward."""

    def test_skip_weekends_checkbox_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_chk_skip_weekends")
        assert isinstance(panel._chk_skip_weekends, ctk.CTkCheckBox)
        panel.destroy()

    def test_skip_holidays_checkbox_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_chk_skip_holidays")
        assert isinstance(panel._chk_skip_holidays, ctk.CTkCheckBox)
        panel.destroy()

    def test_skip_weekends_defaults_to_off(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel._skip_weekends_var.get() == "off"
        panel.destroy()

    def test_skip_holidays_defaults_to_off(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel._skip_holidays_var.get() == "off"
        panel.destroy()

    def test_skip_weekends_persisted_true_restored(self, tk_root):
        settings = {
            "scheduler_skip_weekends": True,
            "scheduler_skip_holidays": False,
            "scheduler_paths": [],
        }
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._skip_weekends_var.get() == "on"
        assert panel._skip_holidays_var.get() == "off"
        panel.destroy()

    def test_skip_holidays_persisted_true_restored(self, tk_root):
        settings = {
            "scheduler_skip_weekends": False,
            "scheduler_skip_holidays": True,
            "scheduler_paths": [],
        }
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._skip_weekends_var.get() == "off"
        assert panel._skip_holidays_var.get() == "on"
        panel.destroy()

    def test_save_config_persists_skip_weekends_true(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._skip_weekends_var.set("on")
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved.get("scheduler_skip_weekends") is True
        panel.destroy()

    def test_save_config_persists_skip_holidays_true(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        panel._skip_holidays_var.set("on")
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved.get("scheduler_skip_holidays") is True
        panel.destroy()

    def test_save_config_persists_skip_flags_false_by_default(self, tk_root):
        panel, mock_mgr = _make_panel(tk_root)
        mock_mgr.set.reset_mock()
        panel._save_config()

        saved = _set_calls(mock_mgr)
        assert saved.get("scheduler_skip_weekends") is False
        assert saved.get("scheduler_skip_holidays") is False
        panel.destroy()

    def test_toggle_on_forwards_skip_weekends_to_start(self, tk_root):
        fired = []

        def on_start(t, p, skip_weekends=False, skip_holidays=False):
            fired.append({"skip_weekends": skip_weekends, "skip_holidays": skip_holidays})

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._skip_weekends_var.set("on")
        panel._skip_holidays_var.set("off")
        panel._enable_var.set("on")
        panel._on_toggle()

        assert len(fired) == 1
        assert fired[0]["skip_weekends"] is True
        assert fired[0]["skip_holidays"] is False
        panel.destroy()

    def test_toggle_on_forwards_skip_holidays_to_start(self, tk_root):
        fired = []

        def on_start(t, p, skip_weekends=False, skip_holidays=False):
            fired.append({"skip_weekends": skip_weekends, "skip_holidays": skip_holidays})

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._skip_weekends_var.set("off")
        panel._skip_holidays_var.set("on")
        panel._enable_var.set("on")
        panel._on_toggle()

        assert len(fired) == 1
        assert fired[0]["skip_weekends"] is False
        assert fired[0]["skip_holidays"] is True
        panel.destroy()

    def test_config_change_forwards_skip_flags(self, tk_root):
        """Changing a skip checkbox live-updates the running scheduler."""
        fired = []

        def on_start(t, p, skip_weekends=False, skip_holidays=False):
            fired.append({"skip_weekends": skip_weekends, "skip_holidays": skip_holidays})

        panel, _ = _make_panel(tk_root, on_start=on_start)
        panel._paths = ["/some/folder"]
        panel._enable_var.set("on")
        panel._skip_weekends_var.set("on")
        panel._skip_holidays_var.set("on")
        panel._on_config_change()

        assert len(fired) == 1
        assert fired[0]["skip_weekends"] is True
        assert fired[0]["skip_holidays"] is True
        panel.destroy()

    def test_persisted_skip_flags_forwarded_on_launch_arm(self, tk_root, tmp_path):
        """A persisted-enabled panel with skip flags must arm with both flags."""
        folder = tmp_path / "ledgers"
        folder.mkdir()
        fired = []

        def on_start(t, p, skip_weekends=False, skip_holidays=False):
            fired.append({"skip_weekends": skip_weekends, "skip_holidays": skip_holidays})

        settings = {
            "scheduler_enabled": True,
            "scheduler_time": "08:00",
            "scheduler_paths": [str(folder)],
            "scheduler_skip_weekends": True,
            "scheduler_skip_holidays": True,
        }
        panel, _ = _make_panel(tk_root, settings=settings, on_start=on_start)

        assert len(fired) == 1, "on_start must fire once on persisted launch"
        assert fired[0]["skip_weekends"] is True
        assert fired[0]["skip_holidays"] is True
        panel.destroy()

    def test_get_config_includes_skip_flags(self, tk_root):
        panel, _ = _make_panel(tk_root)
        panel._skip_weekends_var.set("on")
        panel._skip_holidays_var.set("off")
        cfg = panel.get_config()
        assert "skip_weekends" in cfg
        assert "skip_holidays" in cfg
        assert cfg["skip_weekends"] is True
        assert cfg["skip_holidays"] is False
        panel.destroy()


# ---------------------------------------------------------------------------
# 13. Missing watch folders are KEPT (not silently dropped) on load
#     (finding: scheduler_panel.py:205)
# ---------------------------------------------------------------------------

class TestSchedulerPanelMissingFolders:
    """A persisted folder that no longer resolves must stay in the list,
    visibly flagged, rather than being silently removed — the user otherwise
    keeps believing the scheduler watches it.
    """

    def test_missing_folder_is_retained_in_paths(self, tk_root, tmp_path):
        present = tmp_path / "live"
        present.mkdir()
        missing = tmp_path / "offline_share"  # never created
        settings = {"scheduler_paths": [str(present), str(missing)]}

        panel, _ = _make_panel(tk_root, settings=settings)
        # Both paths survive — the missing one is NOT dropped.
        assert str(present) in panel._paths
        assert str(missing) in panel._paths
        panel.destroy()

    def test_missing_folder_is_tracked_as_missing(self, tk_root, tmp_path):
        present = tmp_path / "live"
        present.mkdir()
        missing = tmp_path / "gone"
        settings = {"scheduler_paths": [str(present), str(missing)]}

        panel, _ = _make_panel(tk_root, settings=settings)
        assert str(missing) in panel._missing_paths
        assert str(present) not in panel._missing_paths
        panel.destroy()

    def test_missing_folder_marked_in_path_list(self, tk_root, tmp_path):
        missing = tmp_path / "gone"
        settings = {"scheduler_paths": [str(missing)]}

        panel, _ = _make_panel(tk_root, settings=settings)
        panel._path_list.configure(state="normal")
        content = panel._path_list.get("1.0", "end")
        panel._path_list.configure(state="disabled")
        # The unavailable marker glyph distinguishes it from a healthy folder.
        assert "⚠" in content
        assert "gone" in content
        panel.destroy()

    def test_valid_paths_excludes_missing(self, tk_root, tmp_path):
        present = tmp_path / "live"
        present.mkdir()
        missing = tmp_path / "gone"
        settings = {"scheduler_paths": [str(present), str(missing)]}

        panel, _ = _make_panel(tk_root, settings=settings)
        valid = panel._valid_paths()
        assert valid == [str(present)]
        panel.destroy()

    def test_status_warns_when_some_folders_missing(self, tk_root, tmp_path):
        present = tmp_path / "live"
        present.mkdir()
        missing = tmp_path / "gone"
        settings = {"scheduler_paths": [str(present), str(missing)]}

        panel, _ = _make_panel(tk_root, settings=settings)
        from gui.theme import get_theme

        panel._update_status()
        text = panel._lbl_status.cget("text")
        # Status reflects the valid count (1) but flags that some are missing
        # via the warning color rather than the all-good success color.
        assert panel._lbl_status.cget("text_color") == get_theme()["warning"]
        assert text != ""
        panel.destroy()

    def test_persisted_enabled_arms_over_valid_subset_only(
        self, tk_root, tmp_path
    ):
        """A persisted-enabled panel with one live + one missing folder must
        arm the scheduler over the LIVE folder only (never the missing one).
        """
        present = tmp_path / "live"
        present.mkdir()
        missing = tmp_path / "gone"
        fired = []
        settings = {
            "scheduler_enabled": True,
            "scheduler_time": "08:30",
            "scheduler_paths": [str(present), str(missing)],
        }
        panel, _ = _make_panel(
            tk_root, settings=settings,
            on_start=lambda t, p, **kw: fired.append((t, p)),
        )
        assert len(fired) == 1
        # Armed over the valid subset, not the full (incl. missing) list.
        assert fired[0][1] == [str(present)]
        assert panel._enable_var.get() == "on"
        panel.destroy()

    def test_persisted_enabled_all_missing_repairs_to_off(
        self, tk_root, tmp_path
    ):
        """If EVERY persisted folder is missing, the scheduler cannot run; it
        must repair to off (but the folders are still listed for the user).
        """
        missing = tmp_path / "gone"
        fired = []
        settings = {
            "scheduler_enabled": True,
            "scheduler_time": "23:00",
            "scheduler_paths": [str(missing)],
        }
        panel, mock_mgr = _make_panel(
            tk_root, settings=settings,
            on_start=lambda t, p, **kw: fired.append((t, p)),
        )
        assert fired == [], "must not arm when no valid folder resolves"
        assert panel._enable_var.get() == "off"
        # Folder is still retained (not dropped) so the user can see/remove it.
        assert str(missing) in panel._paths
        saved = {c.args[0]: c.args[1] for c in mock_mgr.set.call_args_list}
        assert saved.get("scheduler_enabled") is False
        panel.destroy()

    def test_toggle_on_with_only_missing_folder_reverts_off(
        self, tk_root, tmp_path
    ):
        """Flipping ON when the only watch folder is unavailable must refuse to
        arm — a list of only-missing folders counts as zero valid folders.
        """
        missing = tmp_path / "gone"
        fired = []
        panel, _ = _make_panel(
            tk_root, on_start=lambda t, p, **kw: fired.append((t, p))
        )
        panel._paths = [str(missing)]
        panel._recompute_missing()
        panel._enable_var.set("on")
        panel._on_toggle()
        assert panel._enable_var.get() == "off"
        assert fired == []
        panel.destroy()


# ---------------------------------------------------------------------------
# 14. Last-run feedback row (finding: app.py:903 — panel side)
# ---------------------------------------------------------------------------

class TestSchedulerPanelLastRun:
    """The panel renders the persisted scheduler_last_run summary so a
    tray-minimised scheduled fire is no longer invisible in the panel.

    The i18n catalog entry ``sched.last_run`` now ships in the real catalog
    (added by the wave-2 i18n-sync pass). These tests still pin a known value
    for the duration of a test and restore the prior catalog state on teardown
    so they neither depend on the exact wording nor mutate the shared CATALOG
    for any test that runs afterwards.
    """

    @pytest.fixture
    def _last_run_key(self):
        """Pin sched.last_run for a test, restoring the catalog afterwards."""
        from core import i18n

        sentinel = object()
        previous = i18n.CATALOG.get("sched.last_run", sentinel)
        i18n.CATALOG["sched.last_run"] = {
            "en": "Last run: {summary}",
            "th": "ทำงานล่าสุด: {summary}",
        }
        try:
            yield
        finally:
            if previous is sentinel:
                i18n.CATALOG.pop("sched.last_run", None)
            else:
                i18n.CATALOG["sched.last_run"] = previous

    def test_last_run_label_exists(self, tk_root):
        import customtkinter as ctk

        panel, _ = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_last_run")
        assert isinstance(panel._lbl_last_run, ctk.CTkLabel)
        panel.destroy()

    def test_last_run_empty_when_never_run(self, tk_root):
        panel, _ = _make_panel(tk_root)
        assert panel._lbl_last_run.cget("text") == ""
        panel.destroy()

    def test_last_run_rendered_from_persisted_record(
        self, tk_root, _last_run_key
    ):
        settings = {
            "scheduler_paths": [],
            "scheduler_last_run": {
                "success": 7, "fail": 1,
                "summary": "7 OK, 1 failed (04 Jun 23:00)",
            },
        }
        panel, _ = _make_panel(tk_root, settings=settings)
        text = panel._lbl_last_run.cget("text")
        assert "7 OK, 1 failed (04 Jun 23:00)" in text
        panel.destroy()

    def test_last_run_accepts_plain_string_record(
        self, tk_root, _last_run_key
    ):
        settings = {
            "scheduler_paths": [],
            "scheduler_last_run": "failed: network down",
        }
        panel, _ = _make_panel(tk_root, settings=settings)
        assert "failed: network down" in panel._lbl_last_run.cget("text")
        panel.destroy()

    def test_refresh_last_run_picks_up_new_record(
        self, tk_root, _last_run_key
    ):
        """The public refresh hook re-reads settings so the app can update the
        row after a fire without a restart.
        """
        live_settings = {"scheduler_paths": []}
        panel, mock_mgr = _make_panel(tk_root, settings=live_settings)
        assert panel._lbl_last_run.cget("text") == ""
        # Simulate the app persisting a fresh last-run record, then refreshing.
        live_settings["scheduler_last_run"] = {
            "success": 3, "fail": 0, "summary": "3 OK, 0 failed",
        }
        panel.refresh_last_run()
        assert "3 OK, 0 failed" in panel._lbl_last_run.cget("text")
        panel.destroy()

    def test_announce_scheduled_run_refreshes_panel(self, monkeypatch):
        """F77 wiring: a scheduled fire calls the panel's refresh hook
        in-session (the persisted record alone only covers future loads)."""
        from gui.app import BOTExrateApp

        monkeypatch.setattr("gui.app._settings_mgr.set", lambda *a, **k: None)
        app = MagicMock()
        app._tray = None  # no tray on this platform

        BOTExrateApp._announce_scheduled_run(app, success=2, fail=0)

        app.scheduler_panel.refresh_last_run.assert_called_once_with()

    def test_malformed_last_run_record_hides_row(self, tk_root, _last_run_key):
        settings = {
            "scheduler_paths": [],
            "scheduler_last_run": {"success": 1},  # no summary
        }
        panel, _ = _make_panel(tk_root, settings=settings)
        assert panel._lbl_last_run.cget("text") == ""
        panel.destroy()


# ---------------------------------------------------------------------------
# 15. Tooltips on icon/emoji controls (finding: scheduler_panel.py:71)
# ---------------------------------------------------------------------------

class TestSchedulerPanelTooltips:
    """Icon/emoji-bearing controls carry hover tooltips so their meaning is
    discoverable regardless of emoji-glyph rendering across platforms.
    """

    def test_tooltips_registered_for_controls(self, tk_root):
        panel, _ = _make_panel(tk_root)
        # One tooltip each for: toggle, hour, minute, skip-weekends,
        # skip-holidays, remove, add => 7 controls.
        assert len(panel._tooltips) == 7
        panel.destroy()

    def test_tooltip_has_nonempty_text(self, tk_root):
        panel, _ = _make_panel(tk_root)
        for tip in panel._tooltips:
            assert tip._text, "every tooltip must carry text"
        panel.destroy()

    def test_tooltip_show_and_hide_do_not_raise(self, tk_root):
        panel, _ = _make_panel(tk_root)
        tip = panel._tooltips[0]
        tip._show()  # builds the toplevel
        assert tip._tip is not None
        tip._hide()  # tears it down
        assert tip._tip is None
        panel.destroy()

    def test_tooltip_hidden_after_panel_destroy(self, tk_root):
        """Destroying the panel must not leave a dangling tooltip toplevel."""
        panel, _ = _make_panel(tk_root)
        tip = panel._tooltips[0]
        tip._show()
        panel.destroy()
        # The <Destroy> binding fires _hide; the tip is torn down.
        assert tip._tip is None
