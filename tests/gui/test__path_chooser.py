#!/usr/bin/env python3
"""
tests/gui/test__path_chooser.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/_path_chooser.py (choose_path_to_remove).

Strategy
--------
choose_path_to_remove() calls parent.wait_window(dialog) which blocks until
the dialog is destroyed.  In tests we patch wait_window on the tk_root so it
becomes a no-op (or drives a button command), then manually invoke the inner
_confirm / _cancel closures via the dialog's children.

Because CTkToplevel.grab_set() requires a mapped window the dialog is
immediately withdrawn in the wait_window side-effect before we touch it.

All tests require a display; the tk_root fixture skips them on headless CI.
"""

import os
from unittest.mock import patch

import customtkinter as ctk
import pytest

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_buttons(widget) -> list:
    """Recursively collect all CTkButton instances under *widget*."""
    buttons = []
    if isinstance(widget, ctk.CTkButton):
        buttons.append(widget)
    for child in widget.winfo_children():
        buttons.extend(_collect_buttons(child))
    return buttons


def _collect_radio_buttons(widget) -> list:
    """Recursively collect all CTkRadioButton instances under *widget*."""
    radios = []
    if isinstance(widget, ctk.CTkRadioButton):
        radios.append(widget)
    for child in widget.winfo_children():
        radios.extend(_collect_radio_buttons(child))
    return radios


def _collect_labels(widget) -> list:
    """Recursively collect all CTkLabel instances under *widget*."""
    labels = []
    if isinstance(widget, ctk.CTkLabel):
        labels.append(widget)
    for child in widget.winfo_children():
        labels.extend(_collect_labels(child))
    return labels


def _run_chooser_with_action(tk_root, paths: list[str], action: str):
    """
    Call choose_path_to_remove, intercept wait_window, perform *action*
    (``"confirm"`` or ``"cancel"``), and return the function's return value.

    The wait_window side-effect:
      1. Withdraws the dialog so no window appears on screen.
      2. Locates Cancel / Remove buttons by their text label.
      3. Invokes the requested button command.
      4. Returns without blocking.
    """
    from gui.panels._path_chooser import choose_path_to_remove

    captured = {}

    def _fake_wait_window(dialog):
        captured["dialog"] = dialog
        # Hide immediately — no mapping required for command invocation.
        dialog.withdraw()
        dialog.update_idletasks()

        buttons = _collect_buttons(dialog)
        btn_map = {b.cget("text"): b for b in buttons}

        if action == "confirm":
            btn_map["Remove"].invoke()
        else:
            btn_map["Cancel"].invoke()

    with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
        result = choose_path_to_remove(tk_root, paths)

    return result, captured.get("dialog")


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestPathChooserDialogConstruction:
    """Dialog is constructed with the expected title, geometry, and widgets."""

    def test_dialog_created_as_ctk_toplevel(self, tk_root):
        from gui.panels._path_chooser import choose_path_to_remove

        created = {}

        def _fake_wait_window(dialog):
            created["dialog"] = dialog
            dialog.withdraw()
            dialog.update_idletasks()
            # Invoke Cancel so the function returns cleanly.
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, ["/some/path"])

        assert isinstance(created["dialog"], ctk.CTkToplevel)

    def test_dialog_title_is_remove_watch_folder(self, tk_root):
        captured = {}

        def _fake_wait_window(dialog):
            captured["title"] = dialog.title()
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, ["/some/path"])

        assert captured["title"] == "Remove Watch Folder"

    def test_dialog_is_not_resizable(self, tk_root):
        # The source calls dialog.resizable(False, False).
        # Verify both axes are locked — more reliable than geometry on macOS.
        captured = {}

        def _fake_wait_window(dialog):
            captured["resizable"] = dialog.resizable()
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, ["/some/path"])

        # resizable() returns (width_bool, height_bool) — both must be False.
        assert captured["resizable"] == (False, False)

    def test_both_buttons_present(self, tk_root):
        captured = {}

        def _fake_wait_window(dialog):
            captured["btns"] = {b.cget("text") for b in _collect_buttons(dialog)}
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, ["/a", "/b"])

        assert "Cancel" in captured["btns"]
        assert "Remove" in captured["btns"]


class TestPathChooserRadioButtons:
    """One radio button per path; labels use basename or full path."""

    def test_radio_count_matches_paths(self, tk_root):
        paths = ["/watch/alpha", "/watch/beta", "/watch/gamma"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["radios"] = _collect_radio_buttons(dialog)
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, paths)

        assert len(captured["radios"]) == 3

    def test_radio_labels_use_basename(self, tk_root):
        paths = ["/watch/alpha", "/watch/beta"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["radios"] = _collect_radio_buttons(dialog)
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, paths)

        texts = [r.cget("text") for r in captured["radios"]]
        assert any("alpha" in t for t in texts)
        assert any("beta" in t for t in texts)

    def test_radio_label_fallback_to_full_path_when_basename_empty(self, tk_root):
        # A path ending in os.sep has an empty basename; the source falls back
        # to the full path string.
        trailing_sep = "/watch/myfolder" + os.sep
        expected_basename = os.path.basename(trailing_sep)  # "" on POSIX
        captured = {}

        def _fake_wait_window(dialog):
            captured["radios"] = _collect_radio_buttons(dialog)
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, [trailing_sep])

        assert len(captured["radios"]) == 1
        label_text = captured["radios"][0].cget("text")
        if expected_basename == "":
            # Fallback: full path shown
            assert trailing_sep in label_text
        else:
            assert expected_basename in label_text

    def test_first_radio_selected_by_default(self, tk_root):
        paths = ["/watch/first", "/watch/second"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["radios"] = _collect_radio_buttons(dialog)
            dialog.withdraw()
            dialog.update_idletasks()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            choose_path_to_remove(tk_root, paths)

        # The IntVar defaults to 0; the first radio button has value=0.
        radios = captured["radios"]
        # Each CTkRadioButton exposes its variable via ._variable; check the
        # shared IntVar value equals 0 (first option).
        shared_var = radios[0]._variable
        assert shared_var.get() == 0


class TestPathChooserConfirmBehaviour:
    """_confirm() sets result['index'] and destroys the dialog."""

    def test_confirm_returns_selected_index_0(self, tk_root):
        paths = ["/a/one", "/a/two"]
        result, _ = _run_chooser_with_action(tk_root, paths, "confirm")
        assert result == 0

    def test_confirm_returns_correct_index_after_selection_change(self, tk_root):
        paths = ["/a/one", "/a/two", "/a/three"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["dialog"] = dialog
            dialog.withdraw()
            dialog.update_idletasks()
            # Select the second radio button (index 1) before confirming.
            radios = _collect_radio_buttons(dialog)
            radios[1].invoke()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Remove":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            result = choose_path_to_remove(tk_root, paths)

        assert result == 1

    def test_confirm_returns_last_index_when_last_selected(self, tk_root):
        paths = ["/a/one", "/a/two", "/a/three"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["dialog"] = dialog
            dialog.withdraw()
            dialog.update_idletasks()
            radios = _collect_radio_buttons(dialog)
            radios[-1].invoke()  # select the last
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Remove":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            result = choose_path_to_remove(tk_root, paths)

        assert result == len(paths) - 1

    def test_confirm_with_single_path_returns_0(self, tk_root):
        result, _ = _run_chooser_with_action(tk_root, ["/only/path"], "confirm")
        assert result == 0


class TestPathChooserCancelBehaviour:
    """_cancel() destroys the dialog without setting result['index']."""

    def test_cancel_returns_none(self, tk_root):
        result, _ = _run_chooser_with_action(tk_root, ["/a", "/b"], "cancel")
        assert result is None

    def test_cancel_with_single_path_returns_none(self, tk_root):
        result, _ = _run_chooser_with_action(tk_root, ["/only"], "cancel")
        assert result is None

    def test_cancel_after_radio_selection_still_returns_none(self, tk_root):
        paths = ["/x/one", "/x/two"]
        captured = {}

        def _fake_wait_window(dialog):
            captured["dialog"] = dialog
            dialog.withdraw()
            dialog.update_idletasks()
            # Select second radio then cancel — result must still be None.
            radios = _collect_radio_buttons(dialog)
            radios[1].invoke()
            for b in _collect_buttons(dialog):
                if b.cget("text") == "Cancel":
                    b.invoke()
                    break

        from gui.panels._path_chooser import choose_path_to_remove

        with patch.object(tk_root, "wait_window", side_effect=_fake_wait_window):
            result = choose_path_to_remove(tk_root, paths)

        assert result is None
