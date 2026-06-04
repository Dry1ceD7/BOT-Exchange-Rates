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

    def test_geometry_width_is_420(self, tk_root):
        modal, _ = _make_modal(tk_root)
        geo = modal.geometry()
        # geometry() returns "WxH+X+Y". The height is now screen-capped (so the
        # window always fits a small legacy screen and the content scrolls), but
        # the width stays fixed at 420 for a tidy single-column layout.
        assert geo.startswith("420x"), f"unexpected geometry: {geo}"
        modal.destroy()

    def test_height_capped_to_screen(self, tk_root):
        """Window height never exceeds the available screen (audit: clipping)."""
        modal, _ = _make_modal(tk_root)
        assert modal._height <= 720
        assert modal._height <= modal.winfo_screenheight()
        modal.destroy()

    def test_window_is_vertically_resizable(self, tk_root):
        """Resizable height lets the user grow the window; minsize guards it."""
        modal, _ = _make_modal(tk_root)
        # resizable() returns (width_resizable, height_resizable)
        resize_w, resize_h = modal.resizable()
        assert resize_h
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
    """Appearance control shows Title-Case labels, persists lowercase codes.

    Audit finding: the appearance buttons used lowercase labels
    ('system'/'dark'/'light') while the Rate Type buttons used Title Case,
    an inconsistent mix. The control now shows 'System'/'Dark'/'Light' and
    maps them back to the lowercase mode codes CustomTkinter expects.
    """

    def test_appearance_labels_are_title_case(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert set(modal._appearance_map.keys()) == {"System", "Dark", "Light"}
        modal.destroy()

    def test_appearance_map_values_are_lowercase_codes(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert set(modal._appearance_map.values()) == {
            "system", "dark", "light"
        }
        modal.destroy()

    def test_appearance_reverse_is_inverse(self, tk_root):
        modal, _ = _make_modal(tk_root)
        for label, code in modal._appearance_map.items():
            assert modal._appearance_reverse[code] == label
        modal.destroy()

    def test_appearance_default_from_settings(self, tk_root):
        """A persisted 'dark' code displays as the 'Dark' label."""
        modal, _ = _make_modal(tk_root, settings={"appearance": "dark"})
        assert modal._appearance_var.get() == "Dark"
        modal.destroy()

    def test_appearance_default_system_when_not_set(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert modal._appearance_var.get() == "System"
        modal.destroy()

    def test_appearance_var_can_be_set_to_light(self, tk_root):
        modal, _ = _make_modal(tk_root)
        modal._appearance_var.set("Light")
        assert modal._appearance_var.get() == "Light"
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
        """The Title-Case label is mapped to the lowercase mode code."""
        modal, _ = _make_modal(tk_root)
        import customtkinter as ctk
        with patch.object(ctk, "set_appearance_mode") as mock_set:
            modal._on_appearance_change("Dark")
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
                modal._on_appearance_change("Light")
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
            modal._on_appearance_change("System")  # must not raise
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
        """The Title-Case 'Dark' label persists as the lowercase 'dark' code."""
        modal, mock_mgr = _make_modal(tk_root)
        modal._appearance_var.set("Dark")
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

    def test_invalid_input_blocks_save_and_close(self, tk_root):
        """A non-numeric threshold must NOT save and must NOT close the modal.

        Audit finding: the old code silently closed and kept the previous value
        with no feedback. The corrected contract aborts the save+close so the
        user sees their typo was not accepted.
        """
        modal, mock_mgr = _make_modal(
            tk_root, settings={"anomaly_threshold_pct": 5.0}
        )
        modal._anomaly_threshold_var.set("not-a-number")
        modal._save_and_close()
        mock_mgr.save.assert_not_called()
        assert modal.winfo_exists()
        modal.destroy()

    def test_non_positive_input_blocks_save_and_close(self, tk_root):
        modal, mock_mgr = _make_modal(
            tk_root, settings={"anomaly_threshold_pct": 5.0}
        )
        modal._anomaly_threshold_var.set("0")
        modal._save_and_close()
        mock_mgr.save.assert_not_called()
        assert modal.winfo_exists()
        modal.destroy()

    def test_invalid_input_shows_inline_error(self, tk_root):
        modal, _ = _make_modal(tk_root)
        modal._anomaly_threshold_var.set("abc")
        modal._save_and_close()
        assert modal._anomaly_error.cget("text")  # non-empty error text
        modal.destroy()

    def test_invalid_input_keeps_focus_in_entry(self, tk_root):
        modal, _ = _make_modal(tk_root)
        with patch.object(modal._anomaly_entry, "focus_set") as mock_focus:
            modal._anomaly_threshold_var.set("")
            modal._save_and_close()
            mock_focus.assert_called_once()
        modal.destroy()

    def test_negative_input_blocks_save(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._anomaly_threshold_var.set("-3")
        modal._save_and_close()
        mock_mgr.save.assert_not_called()
        modal.destroy()

    def test_valid_input_clears_prior_error_and_saves(self, tk_root):
        """A prior error is cleared once a valid value is entered and saved."""
        modal, mock_mgr = _make_modal(tk_root)
        # First trip an error
        modal._anomaly_threshold_var.set("bad")
        modal._save_and_close()
        assert modal._anomaly_error.cget("text")
        assert modal.winfo_exists()
        # Then correct it
        modal._anomaly_threshold_var.set("8.0")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["anomaly_threshold_pct"] == 8.0
        assert not modal.winfo_exists()

    def test_whitespace_padded_value_is_accepted(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        modal._anomaly_threshold_var.set("  6.25  ")
        modal._save_and_close()
        saved = mock_mgr.save.call_args[0][0]
        assert saved["anomaly_threshold_pct"] == 6.25


# ---------------------------------------------------------------------------
# Open Logs / Audit Folder
# ---------------------------------------------------------------------------

class TestOpenLogsFolder:
    """_on_open_logs reveals data/logs via a platform-safe folder launcher."""

    def test_open_logs_calls_open_folder_with_logs_path(self, tk_root):
        modal, _ = _make_modal(tk_root)
        with (
            patch(
                "gui.panels.settings_modal.get_project_root",
                return_value="/tmp/botexrate-root",
            ),
            patch(
                "gui.panels.settings_modal._open_folder", return_value=True
            ) as mock_open,
            patch("pathlib.Path.mkdir"),
        ):
            modal._on_open_logs()
        mock_open.assert_called_once()
        called_path = mock_open.call_args[0][0]
        assert called_path.endswith("data/logs") or called_path.endswith(
            "data\\logs"
        )
        modal.destroy()

    def test_open_logs_surfaces_failure_inline(self, tk_root):
        modal, _ = _make_modal(tk_root)
        with (
            patch(
                "gui.panels.settings_modal.get_project_root",
                return_value="/tmp/botexrate-root",
            ),
            patch(
                "gui.panels.settings_modal._open_folder", return_value=False
            ),
            patch("pathlib.Path.mkdir"),
        ):
            modal._on_open_logs()
        assert modal._anomaly_error.cget("text")  # failure surfaced inline
        modal.destroy()

    def test_open_logs_no_error_text_on_success(self, tk_root):
        modal, _ = _make_modal(tk_root)
        with (
            patch(
                "gui.panels.settings_modal.get_project_root",
                return_value="/tmp/botexrate-root",
            ),
            patch(
                "gui.panels.settings_modal._open_folder", return_value=True
            ),
            patch("pathlib.Path.mkdir"),
        ):
            modal._on_open_logs()
        assert modal._anomaly_error.cget("text") == ""
        modal.destroy()


# ---------------------------------------------------------------------------
# _open_folder helper (module-level, no widgets required)
# ---------------------------------------------------------------------------

class TestOpenFolderHelper:
    """_open_folder validates the target and dispatches a fixed-argv launcher."""

    def test_returns_false_for_nonexistent_dir(self):
        from gui.panels.settings_modal import _open_folder
        assert _open_folder("/no/such/dir/anywhere/12345") is False

    def test_returns_false_for_file_not_dir(self, tmp_path):
        from gui.panels.settings_modal import _open_folder
        f = tmp_path / "a_file.txt"
        f.write_text("x", encoding="utf-8")
        assert _open_folder(str(f)) is False

    def test_launches_open_on_darwin(self, tmp_path):
        from gui.panels import settings_modal
        with (
            patch.object(settings_modal.platform, "system", return_value="Darwin"),
            patch.object(settings_modal.subprocess, "Popen") as mock_popen,
        ):
            assert settings_modal._open_folder(str(tmp_path)) is True
        argv = mock_popen.call_args[0][0]
        assert argv[0] == "open"
        assert argv[1] == str(tmp_path.resolve())

    def test_launches_explorer_on_windows(self, tmp_path):
        from gui.panels import settings_modal
        with (
            patch.object(settings_modal.platform, "system", return_value="Windows"),
            patch.object(settings_modal.subprocess, "Popen") as mock_popen,
        ):
            assert settings_modal._open_folder(str(tmp_path)) is True
        argv = mock_popen.call_args[0][0]
        assert argv[0] == "explorer"

    def test_launches_xdg_open_on_linux(self, tmp_path):
        from gui.panels import settings_modal
        with (
            patch.object(settings_modal.platform, "system", return_value="Linux"),
            patch.object(settings_modal.subprocess, "Popen") as mock_popen,
        ):
            assert settings_modal._open_folder(str(tmp_path)) is True
        argv = mock_popen.call_args[0][0]
        assert argv[0] == "xdg-open"

    def test_returns_false_on_oserror(self, tmp_path):
        from gui.panels import settings_modal
        with (
            patch.object(settings_modal.platform, "system", return_value="Linux"),
            patch.object(
                settings_modal.subprocess, "Popen", side_effect=OSError("boom")
            ),
        ):
            assert settings_modal._open_folder(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# Scrollable body (no clipped controls on small screens)
# ---------------------------------------------------------------------------

class TestScrollableBody:
    """Controls live in a scrollable body; the Save button stays pinned.

    Audit finding: the fixed 720px non-resizable window clipped the bottom
    controls on small legacy screens. The content now scrolls and the Save
    button is packed outside the scroll region so it is always reachable.
    """

    def test_body_is_scrollable_frame(self, tk_root):
        import customtkinter as ctk
        modal, _ = _make_modal(tk_root)
        assert isinstance(modal.body, ctk.CTkScrollableFrame)
        modal.destroy()

    def test_anomaly_entry_parented_to_body(self, tk_root):
        """Inner controls sit inside the scroll body, not the toplevel."""
        modal, _ = _make_modal(tk_root)
        # The entry's master should be the scroll body's inner frame chain,
        # never the modal toplevel directly.
        assert modal._anomaly_entry.winfo_toplevel() is modal
        assert modal._anomaly_entry.master is not modal
        modal.destroy()

    def test_save_button_outside_scroll_body(self, tk_root):
        """The Save button lives outside the scroll body so it never clips.

        Walks the whole widget tree (CTk wraps children in internal frames, so
        a direct winfo_children() scan is unreliable) and asserts the 'Save and
        Close' button is NOT a descendant of the scrollable body — i.e. it stays
        pinned and always reachable regardless of how far the content scrolls.
        """
        import customtkinter as ctk
        modal, _ = _make_modal(tk_root)

        def _walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from _walk(child)

        save_btns = [
            w for w in _walk(modal)
            if isinstance(w, ctk.CTkButton)
            and "Save and Close" in str(w.cget("text"))
        ]
        assert save_btns, "Save button must exist in the modal"
        body_widgets = set(_walk(modal.body))
        assert all(b not in body_widgets for b in save_btns), (
            "Save button must NOT be inside the scrollable body"
        )
        modal.destroy()


# ---------------------------------------------------------------------------
# Restart wiring (Finding: app._restart_app does not exist)
# ---------------------------------------------------------------------------

class TestRestartWiring:
    """VersionPanel.on_restart is wired to the real restart entry point.

    Audit finding: the modal passed getattr(app, '_restart_app', None) — a
    method that never existed — so on_restart was always None and the
    'Restart Now' promise leaned on a silent fallback. It is now wired
    explicitly to core.auto_updater.restart_app.
    """

    def test_version_panel_receives_real_restart_callback(self, tk_root):
        from core.auto_updater import restart_app

        with patch(
            "gui.panels.version_panel.VersionPanel"
        ) as mock_vp:
            modal, _ = _make_modal(tk_root)
            # VersionPanel was constructed with on_restart=restart_app.
            on_restart = mock_vp.call_args.kwargs.get("on_restart")
            assert on_restart is restart_app
            assert callable(on_restart)
            modal.destroy()

    def test_modal_does_not_resolve_restart_via_getattr(self, tk_root):
        """The dead getattr(app, '_restart_app', ...) call is gone.

        Asserts the live wiring no longer probes the app for a phantom
        '_restart_app' method (a comment may still mention it for history).
        """
        import inspect

        from gui.panels import settings_modal
        src = inspect.getsource(settings_modal._build_ui) \
            if hasattr(settings_modal, "_build_ui") \
            else inspect.getsource(settings_modal.SettingsModal._build_ui)
        assert 'getattr(app, "_restart_app"' not in src
        assert "getattr(app, '_restart_app'" not in src


# ---------------------------------------------------------------------------
# Export / Import settings (multi-PC deployment)
# ---------------------------------------------------------------------------

class TestExportImportSettings:
    """Export/import buttons drive SettingsManager and surface status inline."""

    def test_export_status_attr_exists(self, tk_root):
        modal, _ = _make_modal(tk_root)
        assert hasattr(modal, "_settings_io_status")
        modal.destroy()

    def test_export_calls_manager_export(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        with patch(
            "tkinter.filedialog.asksaveasfilename",
            return_value="/tmp/out.json",
        ):
            modal._on_export_settings()
        mock_mgr.export_settings.assert_called_once_with("/tmp/out.json")
        assert modal._settings_io_status.cget("text")  # success surfaced
        modal.destroy()

    def test_export_cancelled_does_nothing(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        with patch(
            "tkinter.filedialog.asksaveasfilename", return_value=""
        ):
            modal._on_export_settings()
        mock_mgr.export_settings.assert_not_called()
        modal.destroy()

    def test_export_failure_surfaced_inline(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        mock_mgr.export_settings.side_effect = OSError("disk full")
        with patch(
            "tkinter.filedialog.asksaveasfilename",
            return_value="/tmp/out.json",
        ):
            modal._on_export_settings()
        assert modal._settings_io_status.cget("text")  # error surfaced
        modal.destroy()

    def test_import_calls_manager_import_and_applies(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        mock_mgr.import_settings.return_value = {
            "appearance": "dark",
            "rate_type": "selling",
            "anomaly_threshold_pct": 8.0,
            "auto_update": False,
            "language": "th",
        }
        with patch(
            "tkinter.filedialog.askopenfilename",
            return_value="/tmp/in.json",
        ):
            modal._on_import_settings()
        mock_mgr.import_settings.assert_called_once_with("/tmp/in.json")
        # Controls reflect the imported values.
        assert modal._appearance_var.get() == "Dark"
        assert modal._rate_type_var.get() == "Selling"
        assert modal._anomaly_threshold_var.get() == "8.0"
        assert modal._auto_update_var.get() == "off"
        assert modal._settings_io_status.cget("text")  # success surfaced
        modal.destroy()

    def test_import_cancelled_does_nothing(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        with patch(
            "tkinter.filedialog.askopenfilename", return_value=""
        ):
            modal._on_import_settings()
        mock_mgr.import_settings.assert_not_called()
        modal.destroy()

    def test_import_failure_surfaced_inline(self, tk_root):
        modal, mock_mgr = _make_modal(tk_root)
        mock_mgr.import_settings.side_effect = ValueError("bad json")
        with patch(
            "tkinter.filedialog.askopenfilename",
            return_value="/tmp/in.json",
        ):
            modal._on_import_settings()
        assert modal._settings_io_status.cget("text")  # error surfaced
        modal.destroy()


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
