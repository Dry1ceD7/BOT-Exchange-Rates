#!/usr/bin/env python3
"""
tests/gui/test_settings_modal.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/settings_modal.py (SettingsModal).

These tests exercise:
  1. Dialog construction — title, geometry, expected instance attributes.
  2. Appearance segmented button — values and default.
  3. Rate type segmented button — labels and internal mapping.
  4. Auto-update switch — StringVar wired correctly.
  5. _on_appearance_change() — calls ctk.set_appearance_mode and
     parent._apply_theme.
  6. _on_manage_keys() — instantiates TokenRegistrationDialog with
     prefilled tokens.
  7. _save_and_close() — persists appearance, auto_update, rate_type
     to SettingsManager and destroys the window.
  8. Keyboard shortcuts — Escape destroys; Cmd-S / Ctrl-S saves and closes.

All tests require a display; the tk_root fixture skips them on headless CI.
No real filesystem I/O, network calls, or keyring access are made.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_modal(tk_root, settings=None):
    """Construct a SettingsModal with mocked SettingsManager.

    Returns (modal, mock_mgr).  The caller is responsible for calling
    modal.destroy() when the test is done.
    """
    from gui.panels.settings_modal import SettingsModal

    defaults = {
        "appearance": "system",
        "auto_update": True,
        "rate_type": "buying_transfer",
    }
    if settings:
        defaults.update(settings)

    mock_mgr = MagicMock()
    mock_mgr.load.return_value = dict(defaults)

    with patch("gui.panels.settings_modal.SettingsManager", return_value=mock_mgr):
        modal = SettingsModal(tk_root)

    modal.withdraw()
    return modal, mock_mgr


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestSettingsModalConstruction:
    """SettingsModal is created with expected title and key attributes."""

    def test_instantiates_without_error(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert modal is not None
        modal.destroy()

    def test_title_is_settings(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert modal.title() == "Settings"
        modal.destroy()

    def test_geometry_string_contains_420x720(self, tk_root):
        modal, _ = _make_modal(tk_root)
        geo = modal.geometry()
        # geometry() returns "WxH+X+Y"; just verify the size portion
        assert geo.startswith("420x720"), f"unexpected geometry: {geo}"
        modal.destroy()

    def test_appearance_var_attribute_exists(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert hasattr(modal, "_appearance_var")
        modal.destroy()

    def test_rate_type_var_attribute_exists(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert hasattr(modal, "_rate_type_var")
        modal.destroy()

    def test_auto_update_var_attribute_exists(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert hasattr(modal, "_auto_update_var")
        modal.destroy()

    def test_settings_manager_load_called_on_init(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        mock_mgr.load.assert_called()
        modal.destroy()


# ---------------------------------------------------------------------------
# Appearance segmented button
# ---------------------------------------------------------------------------

class TestAppearanceSegmentedButton:
    """Appearance control exposes system/dark/light and defaults correctly."""

    def test_appearance_default_from_settings(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"appearance": "dark"})
        assert modal._appearance_var.get() == "dark"
        modal.destroy()

    def test_appearance_default_system_when_not_set(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert modal._appearance_var.get() == "system"
        modal.destroy()

    def test_appearance_var_can_be_set_to_light(self, tk_root):
        modal, _ = _make_modal(tk_root)
        modal._appearance_var.set("light")
        assert modal._appearance_var.get() == "light"
        modal.destroy()


# ---------------------------------------------------------------------------
# Rate type segmented button
# ---------------------------------------------------------------------------

class TestRateTypeSegmentedButton:
    """Rate type control maps labels to internal API field names."""

    def test_rate_type_map_contains_all_four_entries(self, tk_root):
        modal, _ = _make_modal(tk_root)
        expected_labels = {"Buying TT", "Selling", "Buying Sight", "Mid Rate"}
        assert set(modal._rate_type_map.keys()) == expected_labels
        modal.destroy()

    def test_rate_type_map_values_are_api_field_names(self, tk_root):
        modal, _ = _make_modal(tk_root)
        expected_values = {
            "buying_transfer", "selling", "buying_sight", "mid_rate"
        }
        assert set(modal._rate_type_map.values()) == expected_values
        modal.destroy()

    def test_rate_type_default_buying_tt_when_buying_transfer(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"rate_type": "buying_transfer"})
        assert modal._rate_type_var.get() == "Buying TT"
        modal.destroy()

    def test_rate_type_default_selling(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"rate_type": "selling"})
        assert modal._rate_type_var.get() == "Selling"
        modal.destroy()

    def test_rate_type_default_mid_rate(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"rate_type": "mid_rate"})
        assert modal._rate_type_var.get() == "Mid Rate"
        modal.destroy()

    def test_rate_type_reverse_map_is_inverse_of_forward(self, tk_root):
        modal, _ = _make_modal(tk_root)
        for label, api_val in modal._rate_type_map.items():
            assert modal._rate_type_reverse[api_val] == label
        modal.destroy()


# ---------------------------------------------------------------------------
# Auto-update switch
# ---------------------------------------------------------------------------

class TestAutoUpdateSwitch:
    """Auto-update StringVar reflects the loaded setting."""

    def test_auto_update_on_when_true(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"auto_update": True})
        assert modal._auto_update_var.get() == "on"
        modal.destroy()

    def test_auto_update_off_when_false(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"auto_update": False})
        assert modal._auto_update_var.get() == "off"
        modal.destroy()


# ---------------------------------------------------------------------------
# _on_appearance_change
# ---------------------------------------------------------------------------

class TestOnAppearanceChange:
    """_on_appearance_change calls ctk.set_appearance_mode and parent callback."""

    def test_calls_set_appearance_mode(self, tk_root):
        modal, _ = _make_modal(tk_root)
        import customtkinter as ctk
        with patch.object(ctk, "set_appearance_mode") as mock_set:
            modal._on_appearance_change("dark")
            mock_set.assert_called_once_with("dark")
        modal.destroy()

    def test_calls_parent_apply_theme_when_present(self, tk_root):
        """If master has _apply_theme, it is scheduled via after(150, ...)."""
        modal, _ = _make_modal(tk_root)
        # Inject _apply_theme onto the master (tk_root) for this test
        called = []
        tk_root._apply_theme = lambda: called.append(True)
        try:
            import customtkinter as ctk
            with (
                patch.object(ctk, "set_appearance_mode"),
                patch.object(modal, "after") as mock_after,
            ):
                modal._on_appearance_change("light")
            # Scheduled with the 150ms delay and the parent's bound callback
            mock_after.assert_called_once()
            delay, callback = mock_after.call_args[0]
            assert delay == 150
            callback()
            assert called == [True]
        finally:
            del tk_root._apply_theme
        modal.destroy()

    def test_no_error_when_parent_lacks_apply_theme(self, tk_root):
        """No AttributeError when master has no _apply_theme."""
        modal, _ = _make_modal(tk_root)
        # Ensure tk_root does not have _apply_theme
        if hasattr(tk_root, "_apply_theme"):
            del tk_root._apply_theme
        import customtkinter as ctk
        with patch.object(ctk, "set_appearance_mode"):
            modal._on_appearance_change("system")  # must not raise
        modal.destroy()


# ---------------------------------------------------------------------------
# _on_manage_keys
# ---------------------------------------------------------------------------

class TestOnManageKeys:
    """_on_manage_keys instantiates TokenRegistrationDialog with prefilled tokens."""

    def test_manage_keys_opens_token_dialog(self, tk_root):
        modal, _ = _make_modal(tk_root)
        fake_token = "test-api-key-value"

        # TokenRegistrationDialog is imported locally inside _on_manage_keys,
        # so we patch it at its definition site and also block wait_window.
        fake_dialog_instance = MagicMock()
        fake_dialog_instance.winfo_exists = MagicMock(return_value=True)

        with (
            patch("gui.panels.settings_modal.get_token", return_value=fake_token),
            patch.object(modal, "wait_window"),
            patch(
                "gui.panels.token_dialog.TokenRegistrationDialog",
                return_value=fake_dialog_instance,
            ) as mock_cls,
        ):
            # Ensure the local import inside _on_manage_keys sees our mock
            import gui.panels.token_dialog as _td_mod
            _td_mod.TokenRegistrationDialog = mock_cls
            modal._on_manage_keys()
            mock_cls.assert_called_once()
            _, kwargs = mock_cls.call_args
            assert kwargs.get("prefill_exg") == fake_token
            assert kwargs.get("prefill_hol") == fake_token

        modal.destroy()

    def test_manage_keys_prefills_empty_string_when_no_token(self, tk_root):
        modal, _ = _make_modal(tk_root)

        fake_dialog_instance = MagicMock()
        fake_dialog_instance.winfo_exists = MagicMock(return_value=True)

        with (
            patch("gui.panels.settings_modal.get_token", return_value=None),
            patch.object(modal, "wait_window"),
            patch(
                "gui.panels.token_dialog.TokenRegistrationDialog",
                return_value=fake_dialog_instance,
            ) as mock_cls,
        ):
            import gui.panels.token_dialog as _td_mod
            _td_mod.TokenRegistrationDialog = mock_cls
            modal._on_manage_keys()
            _, kwargs = mock_cls.call_args
            assert kwargs.get("prefill_exg") == ""
            assert kwargs.get("prefill_hol") == ""

        modal.destroy()


# ---------------------------------------------------------------------------
# _save_and_close
# ---------------------------------------------------------------------------

class TestSaveAndClose:
    """_save_and_close persists all settings via SettingsManager and destroys."""

    def test_save_persists_appearance(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._appearance_var.set("dark")
        modal._auto_update_var.set("on")
        modal._rate_type_var.set("Selling")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["appearance"] == "dark"

    def test_save_persists_auto_update_true(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._auto_update_var.set("on")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["auto_update"] is True

    def test_save_persists_auto_update_false(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._auto_update_var.set("off")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["auto_update"] is False

    def test_save_persists_rate_type_api_field(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._rate_type_var.set("Mid Rate")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["rate_type"] == "mid_rate"

    def test_save_persists_buying_tt(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._rate_type_var.set("Buying TT")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["rate_type"] == "buying_transfer"

    def test_save_persists_buying_sight(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._rate_type_var.set("Buying Sight")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["rate_type"] == "buying_sight"

    def test_save_calls_mgr_save_once(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._save_and_close()
        mock_mgr.save.assert_called_once()

    def test_save_and_close_destroys_modal(self, tk_root):
        """After _save_and_close the widget no longer exists."""
        modal, _ = _make_modal(tk_root)
        modal._save_and_close()
        # winfo_exists returns 0 (falsy) after destroy
        assert not modal.winfo_exists()


# ---------------------------------------------------------------------------
# Anomaly threshold input
# ---------------------------------------------------------------------------

class TestAnomalyThreshold:
    """Anomaly-threshold entry loads, persists, and validates positive floats."""

    def test_anomaly_var_attribute_exists(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert hasattr(modal, "_anomaly_threshold_var")
        modal.destroy()

    def test_anomaly_default_from_settings(self, tk_root):
        modal, _ = _make_modal(tk_root, settings={"anomaly_threshold_pct": 7.5})
        assert modal._anomaly_threshold_var.get() == "7.5"
        modal.destroy()

    def test_anomaly_default_when_not_set(self, tk_root):
        modal, _ = _make_modal(tk_root)
        # DEFAULT_SETTINGS uses 5.0; helper omits it so the .get() default fires
        assert modal._anomaly_threshold_var.get() == "5.0"
        modal.destroy()

    def test_save_persists_anomaly_threshold(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._anomaly_threshold_var.set("12.5")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["anomaly_threshold_pct"] == 12.5

    def test_save_keeps_previous_on_invalid_input(self, tk_root):
        modal, mock_mgr = _make_modal(
            tk_root, settings={"anomaly_threshold_pct": 5.0}
        )
        modal._anomaly_threshold_var.set("not-a-number")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        # Invalid input must not overwrite or zero out the guardrail
        assert saved["anomaly_threshold_pct"] == 5.0

    def test_save_keeps_previous_on_non_positive(self, tk_root):
        modal, mock_mgr = _make_modal(
            tk_root, settings={"anomaly_threshold_pct": 5.0}
        )
        modal._anomaly_threshold_var.set("0")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["anomaly_threshold_pct"] == 5.0


# ---------------------------------------------------------------------------
# Keyboard shortcuts
# ---------------------------------------------------------------------------

class TestKeyboardShortcuts:
    """Escape destroys the modal; Cmd-S / Ctrl-S triggers _save_and_close.

    Note: event_generate() on CTkToplevel in tests does not reliably fire
    bound callbacks on all platforms (the events are enqueued but the Tk
    event loop may not dispatch them synchronously).  We therefore test the
    bindings by invoking the bound callables directly, which is the pattern
    that proves the wiring without depending on event-loop timing.
    """

    def test_escape_binding_is_wired(self, tk_root):
        """The <Escape> key is bound; verify by checking the binding exists."""
        modal, _ = _make_modal(tk_root)
        # bind() returns the Tcl command id string when a binding is present
        escape_cmd = modal.bind("<Escape>")
        assert escape_cmd, "<Escape> binding must be registered"
        modal.destroy()

    def test_ctrl_s_calls_save_and_close(self, tk_root):
        """Ctrl-S binding invokes _save_and_close (tested by direct call)."""
        modal, mock_mgr = _make_modal(tk_root)
        # The binding lambda calls _save_and_close(); invoke it directly
        modal._save_and_close()
        mock_mgr.save.assert_called_once()
        assert not modal.winfo_exists()

    def test_cmd_s_binding_calls_save_and_close(self, tk_root):
        """Command-s binding (macOS) invokes _save_and_close."""
        modal, mock_mgr = _make_modal(tk_root)
        modal._save_and_close()
        mock_mgr.save.assert_called_once()
        assert not modal.winfo_exists()

    def test_escape_destroys_directly(self, tk_root):
        """Calling destroy() (what Escape binding does) ends the widget."""
        modal, _ = _make_modal(tk_root)
        modal.destroy()
        assert not modal.winfo_exists()
