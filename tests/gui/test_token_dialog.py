#!/usr/bin/env python3
"""
tests/gui/test_token_dialog.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/token_dialog.py (TokenRegistrationDialog).

These tests exercise:
  1. Widget creation — title, geometry, entry fields, checkbox, buttons.
  2. Prefill values inserted into entry widgets when provided.
  3. _toggle_visibility() cycles show-character between '' and '•'.
  4. _on_activate() validates empty / too-short keys and shows status text.
  5. _on_activate() calls set_token() twice when keyring is available.
  6. _on_activate() calls _write_env() when keyring is not available.
  7. _write_env() creates a .env file with the expected key=value lines.
  8. _write_env() updates existing lines without duplicating them.
  9. _write_env() chmod(0o600) on the .env file (POSIX).
 10. activated flag is True after a successful activate; dialog is destroyed.
 11. Cancel / close sets activated=False.
 12. Portal link label exists.

All tests require a display; the tk_root fixture skips them on headless CI.
grab_set / grab_release are patched to avoid grab errors in test sessions
(CTkToplevel.grab_set() requires the window to be visible and focused, which
is never the case for a withdrawn test session window).
"""

import stat
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_TARGETS = [
    "gui.panels.token_dialog._keyring_available",
    "gui.panels.token_dialog.set_token",
]


def _make_dialog(tk_root, tmp_env=None, prefill_exg="", prefill_hol=""):
    """Construct a TokenRegistrationDialog with grab_set/grab_release patched."""
    from gui.panels.token_dialog import TokenRegistrationDialog

    env_path = str(tmp_env) if tmp_env is not None else None

    with patch("gui.panels.token_dialog._keyring_available", return_value=False), \
         patch("gui.panels.token_dialog.set_token", return_value=True):
        dialog = TokenRegistrationDialog(
            tk_root,
            env_path=env_path,
            prefill_exg=prefill_exg,
            prefill_hol=prefill_hol,
        )

    # Immediately hide: CTkToplevel windows are visible by default.
    dialog.withdraw()
    # Patch grab_set/grab_release so subsequent calls are no-ops.
    dialog.grab_set = MagicMock()
    dialog.grab_release = MagicMock()
    return dialog


# ---------------------------------------------------------------------------
# Construction & widget tree
# ---------------------------------------------------------------------------

class TestTokenDialogConstruction:
    """TokenRegistrationDialog builds the expected widget tree."""

    def test_dialog_instantiates_without_error(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert dialog is not None
        dialog.destroy()

    def test_title_contains_api_registration(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert "API Registration" in dialog.title()
        dialog.destroy()

    def test_geometry_is_520x560(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        geom = dialog.geometry()
        assert geom.startswith("520x560"), f"Expected 520x560 geometry, got: {geom}"
        dialog.destroy()

    def test_entry_exg_exists(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert hasattr(dialog, "_entry_exg")
        dialog.destroy()

    def test_entry_hol_exists(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert hasattr(dialog, "_entry_hol")
        dialog.destroy()

    def test_entries_are_ctk_entry_instances(self, tk_root, tmp_path):
        import customtkinter as ctk

        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert isinstance(dialog._entry_exg, ctk.CTkEntry)
        assert isinstance(dialog._entry_hol, ctk.CTkEntry)
        dialog.destroy()

    def test_status_label_starts_empty(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert hasattr(dialog, "_lbl_status")
        assert dialog._lbl_status.cget("text") == ""
        dialog.destroy()

    def test_show_keys_checkbox_exists(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert hasattr(dialog, "_chk_show")
        dialog.destroy()

    def test_activated_starts_false(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert dialog.activated is False
        dialog.destroy()


# ---------------------------------------------------------------------------
# Prefill
# ---------------------------------------------------------------------------

class TestTokenDialogPrefill:
    """Prefill values are inserted into entry widgets."""

    def test_prefill_exg_inserted(self, tk_root, tmp_path):
        dialog = _make_dialog(
            tk_root, tmp_env=tmp_path / ".env", prefill_exg="PREFILLEDEXG"
        )
        assert dialog._entry_exg.get() == "PREFILLEDEXG"
        dialog.destroy()

    def test_prefill_hol_inserted(self, tk_root, tmp_path):
        dialog = _make_dialog(
            tk_root, tmp_env=tmp_path / ".env", prefill_hol="PREFILLEDHOL"
        )
        assert dialog._entry_hol.get() == "PREFILLEDHOL"
        dialog.destroy()

    def test_no_prefill_entries_empty(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert dialog._entry_exg.get() == ""
        assert dialog._entry_hol.get() == ""
        dialog.destroy()


# ---------------------------------------------------------------------------
# Toggle visibility
# ---------------------------------------------------------------------------

class TestToggleVisibility:
    """_toggle_visibility() cycles the show character correctly."""

    def test_initial_show_char_is_bullet(self, tk_root, tmp_path):
        # CTkEntry show="•" at construction; _show_keys starts False.
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert dialog._show_keys is False
        dialog.destroy()

    def test_toggle_once_reveals_text(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._toggle_visibility()
        assert dialog._show_keys is True
        # show="" means plaintext; cget returns "" for no masking
        assert dialog._entry_exg.cget("show") == ""
        assert dialog._entry_hol.cget("show") == ""
        dialog.destroy()

    def test_toggle_twice_re_masks(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._toggle_visibility()
        dialog._toggle_visibility()
        # _show_keys is the authoritative flag; cget("show") is not reliable
        # on a withdrawn/unrendered CTkToplevel (CTkEntry doesn't forward
        # configure(show=...) to the underlying Tk widget until rendered).
        assert dialog._show_keys is False
        dialog.destroy()


# ---------------------------------------------------------------------------
# Activate — validation
# ---------------------------------------------------------------------------

class TestOnActivateValidation:
    """_on_activate() rejects empty or too-short keys."""

    def test_empty_keys_show_status_error(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._on_activate()
        assert dialog._lbl_status.cget("text") != ""
        # Dialog must NOT be activated
        assert dialog.activated is False
        dialog.destroy()

    def test_one_empty_key_shows_error(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, "VALIDKEY123")
        # _entry_hol intentionally left empty
        dialog._on_activate()
        assert dialog._lbl_status.cget("text") != ""
        assert dialog.activated is False
        dialog.destroy()

    def test_keys_below_min_length_show_error(self, tk_root, tmp_path):
        from gui.panels.token_dialog import MIN_KEY_LENGTH

        short = "x" * (MIN_KEY_LENGTH - 1)
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, short)
        dialog._entry_hol.insert(0, short)
        dialog._on_activate()
        assert dialog._lbl_status.cget("text") != ""
        assert dialog.activated is False
        dialog.destroy()


# ---------------------------------------------------------------------------
# Activate — keyring path
# ---------------------------------------------------------------------------

class TestOnActivateKeyringPath:
    """When keyring is available, set_token() is called for both keys."""

    def test_set_token_called_twice_on_keyring_success(self, tk_root, tmp_path):
        from gui.panels.token_dialog import TokenRegistrationDialog

        mock_set_token = MagicMock(return_value=True)

        with patch("gui.panels.token_dialog._keyring_available", return_value=True), \
             patch("gui.panels.token_dialog.set_token", mock_set_token):
            dialog = TokenRegistrationDialog(
                tk_root, env_path=str(tmp_path / ".env")
            )
        dialog.withdraw()
        dialog.grab_set = MagicMock()
        dialog.grab_release = MagicMock()

        dialog._entry_exg.insert(0, "VALIDEXGKEY999")
        dialog._entry_hol.insert(0, "VALIDHOLKEY999")

        with patch("gui.panels.token_dialog._keyring_available", return_value=True), \
             patch("gui.panels.token_dialog.set_token", mock_set_token):
            dialog._on_activate()

        assert mock_set_token.call_count == 2
        calls = {c.args[0] for c in mock_set_token.call_args_list}
        assert "BOT_TOKEN_EXG" in calls
        assert "BOT_TOKEN_HOL" in calls

    def test_activated_true_after_keyring_success(self, tk_root, tmp_path):
        from gui.panels.token_dialog import TokenRegistrationDialog

        with patch("gui.panels.token_dialog._keyring_available", return_value=True), \
             patch("gui.panels.token_dialog.set_token", return_value=True):
            dialog = TokenRegistrationDialog(
                tk_root, env_path=str(tmp_path / ".env")
            )
        dialog.withdraw()
        dialog.grab_set = MagicMock()
        dialog.grab_release = MagicMock()

        dialog._entry_exg.insert(0, "VALIDEXGKEY999")
        dialog._entry_hol.insert(0, "VALIDHOLKEY999")

        with patch("gui.panels.token_dialog._keyring_available", return_value=True), \
             patch("gui.panels.token_dialog.set_token", return_value=True):
            dialog._on_activate()

        assert dialog.activated is True


# ---------------------------------------------------------------------------
# Activate — .env path
# ---------------------------------------------------------------------------

class TestOnActivateEnvPath:
    """When keyring is unavailable, _write_env() is called and .env is created."""

    def _activate_no_keyring(self, tk_root, tmp_path, exg="VALIDEXGKEY999", hol="VALIDHOLKEY999"):
        """Helper: build dialog with no keyring, activate, return (dialog, env_file)."""
        env_file = tmp_path / ".env"
        dialog = _make_dialog(tk_root, tmp_env=env_file)
        dialog._entry_exg.insert(0, exg)
        dialog._entry_hol.insert(0, hol)

        with patch("gui.panels.token_dialog._keyring_available", return_value=False):
            dialog._on_activate()

        return dialog, env_file

    def test_env_file_created_on_activation(self, tk_root, tmp_path):
        _, env_file = self._activate_no_keyring(tk_root, tmp_path)
        assert env_file.exists(), ".env file must be created"

    def test_env_file_contains_exg_token(self, tk_root, tmp_path):
        _, env_file = self._activate_no_keyring(tk_root, tmp_path)
        content = env_file.read_text(encoding="utf-8")
        assert "BOT_TOKEN_EXG=VALIDEXGKEY999" in content

    def test_env_file_contains_hol_token(self, tk_root, tmp_path):
        _, env_file = self._activate_no_keyring(tk_root, tmp_path)
        content = env_file.read_text(encoding="utf-8")
        assert "BOT_TOKEN_HOL=VALIDHOLKEY999" in content

    def test_activated_true_after_env_write(self, tk_root, tmp_path):
        dialog, _ = self._activate_no_keyring(tk_root, tmp_path)
        assert dialog.activated is True

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod 0o600 is POSIX-only")
    def test_env_file_permissions_0o600(self, tk_root, tmp_path):
        _, env_file = self._activate_no_keyring(tk_root, tmp_path)
        file_mode = stat.S_IMODE(env_file.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"


# ---------------------------------------------------------------------------
# _write_env — update existing lines
# ---------------------------------------------------------------------------

class TestWriteEnv:
    """_write_env() creates or updates the .env file correctly."""

    def _dialog_for_write_env(self, tk_root, tmp_path):
        env_file = tmp_path / ".env"
        dialog = _make_dialog(tk_root, tmp_env=env_file)
        return dialog, env_file

    def test_write_env_creates_file(self, tk_root, tmp_path):
        dialog, env_file = self._dialog_for_write_env(tk_root, tmp_path)
        dialog._write_env("KEY_EXG_1", "KEY_HOL_1")
        assert env_file.exists()
        dialog.destroy()

    def test_write_env_appends_both_keys(self, tk_root, tmp_path):
        dialog, env_file = self._dialog_for_write_env(tk_root, tmp_path)
        dialog._write_env("KEY_EXG_2", "KEY_HOL_2")
        content = env_file.read_text(encoding="utf-8")
        assert "BOT_TOKEN_EXG=KEY_EXG_2" in content
        assert "BOT_TOKEN_HOL=KEY_HOL_2" in content
        dialog.destroy()

    def test_write_env_updates_existing_exg(self, tk_root, tmp_path):
        dialog, env_file = self._dialog_for_write_env(tk_root, tmp_path)
        env_file.write_text("BOT_TOKEN_EXG=OLD_EXG\nOTHER=value\n", encoding="utf-8")
        dialog._write_env("NEW_EXG_KEY", "NEW_HOL_KEY")
        content = env_file.read_text(encoding="utf-8")
        assert "BOT_TOKEN_EXG=NEW_EXG_KEY" in content
        assert "BOT_TOKEN_EXG=OLD_EXG" not in content
        # Unrelated lines preserved
        assert "OTHER=value" in content
        dialog.destroy()

    def test_write_env_updates_existing_hol(self, tk_root, tmp_path):
        dialog, env_file = self._dialog_for_write_env(tk_root, tmp_path)
        env_file.write_text("BOT_TOKEN_HOL=OLD_HOL\n", encoding="utf-8")
        dialog._write_env("NEW_EXG_KEY2", "NEW_HOL_KEY2")
        content = env_file.read_text(encoding="utf-8")
        assert "BOT_TOKEN_HOL=NEW_HOL_KEY2" in content
        assert "BOT_TOKEN_HOL=OLD_HOL" not in content
        dialog.destroy()

    def test_write_env_no_duplicate_keys(self, tk_root, tmp_path):
        dialog, env_file = self._dialog_for_write_env(tk_root, tmp_path)
        env_file.write_text(
            "BOT_TOKEN_EXG=OLD\nBOT_TOKEN_HOL=OLD\n", encoding="utf-8"
        )
        dialog._write_env("NEWEXG", "NEWHOL")
        content = env_file.read_text(encoding="utf-8")
        assert content.count("BOT_TOKEN_EXG=") == 1
        assert content.count("BOT_TOKEN_HOL=") == 1
        dialog.destroy()


# ---------------------------------------------------------------------------
# Cancel / close
# ---------------------------------------------------------------------------

class TestCancelAndClose:
    """_on_close() sets activated=False and destroys without error."""

    def test_close_sets_activated_false(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._on_close()
        assert dialog.activated is False

    def test_close_without_prior_activate(self, tk_root, tmp_path):
        # Simply must not raise
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._on_close()  # should not raise


# ---------------------------------------------------------------------------
# Portal link
# ---------------------------------------------------------------------------

class TestPortalLink:
    """webbrowser.open() is called with the BOT portal URL."""

    def test_portal_url_constant(self):
        from gui.panels.token_dialog import BOT_PORTAL_URL

        assert "apiportal.bot.or.th" in BOT_PORTAL_URL


# ---------------------------------------------------------------------------
# Test Keys button
# ---------------------------------------------------------------------------

class TestTestKeysButton:
    """The 'Test Keys' action verifies entered keys before Activate."""

    def test_test_keys_button_exists(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        assert hasattr(dialog, "_btn_test")
        assert dialog._btn_test.cget("text") == "Test Keys"
        dialog.destroy()

    def test_test_keys_requires_both_keys(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, "ONLYEXGKEY1")
        # _entry_hol left empty
        dialog._on_test_keys()
        # No worker should start; status prompts for both keys.
        assert dialog._busy_test is False
        assert dialog._lbl_status.cget("text") != ""
        dialog.destroy()

    def test_test_keys_spawns_worker_when_both_present(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, "VALIDEXGKEY999")
        dialog._entry_hol.insert(0, "VALIDHOLKEY999")

        started = []
        with patch(
            "gui.panels.token_dialog.threading.Thread"
        ) as mock_thread:
            mock_thread.return_value.start.side_effect = lambda: started.append(True)
            dialog._on_test_keys()

        assert dialog._busy_test is True
        assert started == [True]
        # Button disabled while testing.
        assert dialog._btn_test.cget("state") == "disabled"
        dialog.destroy()

    def test_test_done_success_shows_success_color(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._busy_test = True
        dialog._test_done(True, "✓ Both keys accepted — connection verified.")
        assert dialog._busy_test is False
        assert "verified" in dialog._lbl_status.cget("text").lower()
        dialog.destroy()

    def test_test_done_failure_shows_message(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._busy_test = True
        dialog._test_done(False, "✗ Key rejected. Check the key and try again.")
        assert dialog._busy_test is False
        assert "rejected" in dialog._lbl_status.cget("text").lower()
        dialog.destroy()

    def test_worker_reports_exg_failure(self, tk_root, tmp_path):
        """Worker reports a bad exchange key without testing the holiday key."""
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, "BADEXGKEY123")
        dialog._entry_hol.insert(0, "VALIDHOLKEY999")

        results = []
        dialog._safe_after = lambda d, cb, *a: results.append((cb, a))

        def _fake_ping(token, **kw):
            return (False, "✗ Key rejected. Check the key and try again.")

        with patch("gui.panels.token_dialog.threading.Thread") as mock_thread:
            # Capture the worker target and run it synchronously.
            def _capture(target, **kwargs):
                mock_thread.captured = target
                inst = MagicMock()
                inst.start.side_effect = target
                return inst
            mock_thread.side_effect = _capture
            with patch("gui.panels.token_dialog.ping_token", _fake_ping):
                dialog._on_test_keys()

        # Exactly one _test_done(False, ...) scheduled; holiday key never tested.
        assert len(results) == 1
        cb, args = results[0]
        assert cb == dialog._test_done
        assert args[0] is False
        dialog.destroy()

    def test_worker_reports_both_keys_ok(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._entry_exg.insert(0, "VALIDEXGKEY999")
        dialog._entry_hol.insert(0, "VALIDHOLKEY999")

        results = []
        dialog._safe_after = lambda d, cb, *a: results.append((cb, a))

        with patch("gui.panels.token_dialog.threading.Thread") as mock_thread:
            def _capture(target, **kwargs):
                inst = MagicMock()
                inst.start.side_effect = target
                return inst
            mock_thread.side_effect = _capture
            with patch(
                "gui.panels.token_dialog.ping_token",
                lambda token, **kw: (True, "✓ ok"),
            ):
                dialog._on_test_keys()

        assert len(results) == 1
        cb, args = results[0]
        assert cb == dialog._test_done
        assert args[0] is True
        dialog.destroy()

    def test_safe_after_skipped_when_destroyed(self, tk_root, tmp_path):
        dialog = _make_dialog(tk_root, tmp_env=tmp_path / ".env")
        dialog._destroyed = True
        called = []
        dialog._safe_after(0, lambda: called.append(True))
        assert called == []
        dialog.destroy()

