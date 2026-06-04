#!/usr/bin/env python3
"""
tests/gui/test_exrate_dialog.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/exrate_dialog.py (show_exrate_dialog).

show_exrate_dialog is a function-based dialog: it creates a CTkToplevel,
builds all widgets as local variables, and wires inner callbacks.  Because
all dialog state is local to the function we use two techniques:

1. Patch ctk.CTkToplevel.__init__ to (a) capture the instance and
   (b) immediately withdraw it so the window never appears.
2. Patch ctk.CTkToplevel.grab_set to a no-op so the test process is not
   blocked on a modal grab that requires a visible window.

All tests require a display; the tk_root fixture skips them on headless CI.
No real filesystem I/O, network, or threads are started.
"""

from datetime import date
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.gui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_dialog(app):
    """Call show_exrate_dialog and return the captured CTkToplevel instance.

    Patches:
    - CTkToplevel.__init__: captures self, then withdraws the window.
    - CTkToplevel.grab_set: no-op (prevents modal block without focus).
    - CTkToplevel.transient: no-op (avoids WM complaints in tests).
    - CTkToplevel.update_idletasks: no-op (prevents geometry recalc errors).
    """
    import customtkinter as ctk

    from gui.panels.exrate_dialog import show_exrate_dialog

    captured = []
    _orig_init = ctk.CTkToplevel.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.withdraw()
        captured.append(self)

    with (
        patch.object(ctk.CTkToplevel, "__init__", _patched_init),
        patch.object(ctk.CTkToplevel, "grab_set", lambda self: None),
        patch.object(ctk.CTkToplevel, "transient", lambda self, *a: None),
        patch.object(ctk.CTkToplevel, "update_idletasks", lambda self: None),
    ):
        show_exrate_dialog(app)

    assert captured, "CTkToplevel was not instantiated by show_exrate_dialog"
    return captured[0]


def _find_button(dialog, text_fragment: str):
    """Return the first CTkButton whose text contains *text_fragment*."""
    import customtkinter as ctk

    for widget in dialog.winfo_children():
        if isinstance(widget, ctk.CTkButton) and text_fragment.lower() in str(widget.cget("text")).lower():
            return widget
        # CTk frames can contain nested widgets
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkButton) and text_fragment.lower() in str(child.cget("text")).lower():
                return child
    return None


def _collect_checkboxes(dialog):
    """Recursively collect all CTkCheckBox instances from the dialog tree."""
    import customtkinter as ctk

    result = []

    def _walk(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkCheckBox):
                result.append(child)
            _walk(child)

    _walk(dialog)
    return result


def _collect_switches(dialog):
    """Recursively collect all CTkSwitch instances from the dialog tree."""
    import customtkinter as ctk

    result = []

    def _walk(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkSwitch):
                result.append(child)
            _walk(child)

    _walk(dialog)
    return result


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestExrateDialogCreation:
    """Dialog is created with the correct title and geometry."""

    def test_dialog_is_ctk_toplevel(self, tk_root):
        import customtkinter as ctk

        dialog = _open_dialog(tk_root)
        assert isinstance(dialog, ctk.CTkToplevel)
        dialog.destroy()

    def test_dialog_title(self, tk_root):
        dialog = _open_dialog(tk_root)
        assert dialog.title() == "Create ExRate File"
        dialog.destroy()

    def test_dialog_geometry_440x680_set(self, tk_root):
        """The source calls dialog.geometry('440x680') at construction.
        On a withdrawn window the WM may not apply it before update_idletasks
        runs, so we verify the call was made rather than the resulting string.
        """
        import customtkinter as ctk

        geometry_calls = []
        orig_geometry = ctk.CTkToplevel.geometry

        def _spy_geometry(self, *args, **kwargs):
            if args:
                geometry_calls.append(args[0])
            return orig_geometry(self, *args, **kwargs)

        with patch.object(ctk.CTkToplevel, "geometry", _spy_geometry):
            dialog = _open_dialog(tk_root)

        assert any("440x680" in str(c) for c in geometry_calls), (
            f"dialog.geometry('440x680...') was never called; calls={geometry_calls}"
        )
        dialog.destroy()


class TestExrateDialogCurrencyCheckboxes:
    """Currency checkboxes are built correctly with the right defaults."""

    def test_nine_currency_checkboxes_exist(self, tk_root):
        from gui.panels.exrate_dialog import EXRATE_CURRENCIES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        # 9 currency + 4 rate type checkboxes = 13 total; filter by label
        ccy_boxes = [
            cb for cb in checkboxes
            if str(cb.cget("text")) in EXRATE_CURRENCIES
        ]
        assert len(ccy_boxes) == len(EXRATE_CURRENCIES) == 9
        dialog.destroy()

    def test_usd_default_on(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        usd_boxes = [cb for cb in checkboxes if cb.cget("text") == "USD"]
        assert usd_boxes, "USD checkbox not found"
        assert usd_boxes[0].get() == 1, "USD must be checked by default"
        dialog.destroy()

    def test_eur_default_on(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        eur_boxes = [cb for cb in checkboxes if cb.cget("text") == "EUR"]
        assert eur_boxes, "EUR checkbox not found"
        assert eur_boxes[0].get() == 1, "EUR must be checked by default"
        dialog.destroy()

    def test_non_default_currency_off(self, tk_root):
        """GBP, JPY, etc. should be unchecked by default."""
        from gui.panels.exrate_dialog import EXRATE_CURRENCIES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        defaults_on = {"USD", "EUR"}
        for cb in checkboxes:
            label = str(cb.cget("text"))
            if label in EXRATE_CURRENCIES and label not in defaults_on:
                assert cb.get() == 0, f"{label} should be unchecked by default"
        dialog.destroy()


class TestExrateDialogRateTypeCheckboxes:
    """Rate type checkboxes are built with correct labels and defaults."""

    def test_four_rate_type_checkboxes_exist(self, tk_root):
        from gui.panels.exrate_dialog import EXRATE_RATE_TYPES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        rate_labels = set(EXRATE_RATE_TYPES.keys())
        rate_boxes = [
            cb for cb in checkboxes
            if str(cb.cget("text")) in rate_labels
        ]
        assert len(rate_boxes) == len(EXRATE_RATE_TYPES) == 4
        dialog.destroy()

    def test_buying_tt_default_on(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        boxes = [cb for cb in checkboxes if cb.cget("text") == "Buying TT"]
        assert boxes, "'Buying TT' checkbox not found"
        assert boxes[0].get() == 1, "'Buying TT' must be checked by default"
        dialog.destroy()

    def test_selling_default_on(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        boxes = [cb for cb in checkboxes if cb.cget("text") == "Selling"]
        assert boxes, "'Selling' checkbox not found"
        assert boxes[0].get() == 1, "'Selling' must be checked by default"
        dialog.destroy()

    def test_buying_sight_default_off(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        boxes = [cb for cb in checkboxes if cb.cget("text") == "Buying Sight"]
        assert boxes, "'Buying Sight' checkbox not found"
        assert boxes[0].get() == 0, "'Buying Sight' must be unchecked by default"
        dialog.destroy()

    def test_mid_rate_default_off(self, tk_root):
        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        boxes = [cb for cb in checkboxes if cb.cget("text") == "Mid Rate"]
        assert boxes, "'Mid Rate' checkbox not found"
        assert boxes[0].get() == 0, "'Mid Rate' must be unchecked by default"
        dialog.destroy()


class TestExrateDialogDateModeSwitch:
    """Date mode switch defaults to 'auto'."""

    def test_date_switch_exists(self, tk_root):
        dialog = _open_dialog(tk_root)
        switches = _collect_switches(dialog)
        assert switches, "CTkSwitch for date mode not found"
        dialog.destroy()

    def test_date_switch_default_is_auto(self, tk_root):
        """Switch off-value is 'auto'; the default variable value is 'auto'."""
        dialog = _open_dialog(tk_root)
        switches = _collect_switches(dialog)
        assert switches, "CTkSwitch for date mode not found"
        sw = switches[0]
        # When switch is OFF (default), variable == offvalue == "auto"
        assert sw.get() == "auto", (
            f"Date mode switch should default to 'auto', got {sw.get()!r}"
        )
        dialog.destroy()

    def test_date_switch_text_fragment(self, tk_root):
        dialog = _open_dialog(tk_root)
        switches = _collect_switches(dialog)
        assert switches, "CTkSwitch not found"
        text = str(switches[0].cget("text"))
        assert "manual" in text.lower() or "date" in text.lower(), (
            f"Switch text should describe manual date selection, got {text!r}"
        )
        dialog.destroy()


class TestExrateDialogCreateButton:
    """'Create ExRate File' button is present."""

    def test_create_button_exists(self, tk_root):
        dialog = _open_dialog(tk_root)
        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None, "'Create ExRate File' button not found"
        dialog.destroy()

    def test_create_button_has_command(self, tk_root):
        dialog = _open_dialog(tk_root)
        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None
        # CTkButton stores its command; it should be callable
        cmd = btn.cget("command")
        assert callable(cmd), "Create button command must be callable"
        dialog.destroy()


class TestExrateDialogOnCreateValidation:
    """_on_create() validation: error label shows when selection is empty."""

    def _get_error_label(self, dialog):
        """Return the error CTkLabel (empty text at init, error_text color)."""
        import customtkinter as ctk

        from gui.theme import get_theme

        t = get_theme()
        error_color = t["error_text"]
        for widget in dialog.winfo_children():
            if isinstance(widget, ctk.CTkLabel):
                color = str(widget.cget("text_color"))
                if color == error_color:
                    return widget
        return None

    def test_error_label_starts_empty(self, tk_root):
        dialog = _open_dialog(tk_root)
        lbl = self._get_error_label(dialog)
        assert lbl is not None, "Error label not found in dialog"
        assert lbl.cget("text") == "", "Error label must start empty"
        dialog.destroy()

    def test_no_currency_selected_shows_error(self, tk_root):
        """Unchecking all currencies then pressing Create shows an error."""
        from gui.panels.exrate_dialog import EXRATE_CURRENCIES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        for cb in checkboxes:
            if str(cb.cget("text")) in EXRATE_CURRENCIES:
                cb.deselect()

        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None
        # Invoke the button command — dialog must NOT be destroyed (validation fail)
        cmd = btn.cget("command")
        cmd()  # should set error text and return without destroying

        lbl = self._get_error_label(dialog)
        assert lbl is not None
        assert "currency" in lbl.cget("text").lower(), (
            f"Expected currency error message, got {lbl.cget('text')!r}"
        )
        dialog.destroy()

    def test_no_rate_type_selected_shows_error(self, tk_root):
        """Unchecking all rate types then pressing Create shows an error."""
        from gui.panels.exrate_dialog import EXRATE_RATE_TYPES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)
        rate_labels = set(EXRATE_RATE_TYPES.keys())
        # Ensure at least one currency is selected (defaults: USD/EUR are on)
        # Uncheck all rate types
        for cb in checkboxes:
            if str(cb.cget("text")) in rate_labels:
                cb.deselect()

        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None
        cmd = btn.cget("command")
        cmd()  # should set error text and return

        lbl = self._get_error_label(dialog)
        assert lbl is not None
        assert "rate" in lbl.cget("text").lower(), (
            f"Expected rate type error message, got {lbl.cget('text')!r}"
        )
        dialog.destroy()

    def test_error_label_cleared_on_next_call(self, tk_root):
        """Each _on_create() call clears the previous error first."""
        from gui.panels.exrate_dialog import EXRATE_CURRENCIES, EXRATE_RATE_TYPES

        dialog = _open_dialog(tk_root)
        checkboxes = _collect_checkboxes(dialog)

        # First: uncheck all currencies to trigger error
        for cb in checkboxes:
            if str(cb.cget("text")) in EXRATE_CURRENCIES:
                cb.deselect()

        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None
        cmd = btn.cget("command")
        cmd()  # sets error

        lbl = self._get_error_label(dialog)
        assert lbl is not None
        first_error = lbl.cget("text")
        assert first_error != "", "First call should have set an error"

        # Second call with all rate types unchecked but one currency restored
        for cb in checkboxes:
            if str(cb.cget("text")) == "USD":
                cb.select()
        for cb in checkboxes:
            if str(cb.cget("text")) in set(EXRATE_RATE_TYPES.keys()):
                cb.deselect()

        cmd()  # error cleared then new error set (rate type error)
        second_error = lbl.cget("text")
        # Error label was cleared (even if it now shows a different message)
        assert second_error != first_error, (
            "Error label should be updated on each _on_create() call"
        )
        dialog.destroy()


class TestExrateDialogOnCreateCallsWorker:
    """_on_create() with valid inputs destroys dialog and starts the worker."""

    def test_on_create_valid_destroys_dialog_and_calls_create_file(self, tk_root):
        """With valid selections _on_create destroys the dialog and calls
        _create_exrate_file in a separate thread.  We mock _create_exrate_file
        to prevent real filesystem/network access."""
        dialog = _open_dialog(tk_root)
        # Defaults: USD + EUR checked, Buying TT + Selling checked -> valid

        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None

        called_with = []

        with patch(
            "gui.panels.exrate_dialog._create_exrate_file",
            side_effect=lambda *a, **kw: called_with.append((a, kw)),
        ):
            cmd = btn.cget("command")
            cmd()  # should destroy dialog and call _create_exrate_file

        # Dialog should be destroyed; winfo_exists() returns 0 for destroyed windows
        try:
            exists = dialog.winfo_exists()
        except Exception:
            exists = 0
        assert exists == 0, "Dialog must be destroyed after valid _on_create()"

        assert called_with, "_create_exrate_file must be called on valid input"
        args, kwargs = called_with[0]
        # First positional arg is `app` (tk_root here)
        assert args[0] is tk_root
        # currencies list must contain USD and EUR
        currencies = args[1]
        assert "USD" in currencies
        assert "EUR" in currencies
        # date_range is None in auto mode
        assert kwargs.get("date_range") is None

    def test_on_create_auto_mode_passes_none_date_range(self, tk_root):
        """Auto date mode must pass date_range=None to _create_exrate_file."""
        dialog = _open_dialog(tk_root)
        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None

        captured_kwargs = {}

        with patch(
            "gui.panels.exrate_dialog._create_exrate_file",
            side_effect=lambda *a, **kw: captured_kwargs.update(
                {"args": a, "kwargs": kw}
            ),
        ):
            btn.cget("command")()

        assert captured_kwargs.get("kwargs", {}).get("date_range") is None

    def test_on_create_manual_mode_passes_date_tuple(self, tk_root):
        """Manual date mode must pass a (date, date) tuple to _create_exrate_file."""
        dialog = _open_dialog(tk_root)

        # Toggle the switch to manual
        switches = _collect_switches(dialog)
        assert switches, "Date mode switch not found"
        sw = switches[0]
        sw.select()  # sets variable to onvalue="manual"
        # Invoke the switch command to trigger _toggle_date_mode()
        sw_cmd = sw.cget("command")
        if callable(sw_cmd):
            sw_cmd()

        btn = _find_button(dialog, "Create ExRate")
        assert btn is not None

        captured = {}

        with patch(
            "gui.panels.exrate_dialog._create_exrate_file",
            side_effect=lambda *a, **kw: captured.update(
                {"args": a, "kwargs": kw}
            ),
        ):
            btn.cget("command")()

        dr = captured.get("kwargs", {}).get("date_range")
        assert dr is not None, "Manual mode must pass a date_range tuple"
        assert isinstance(dr, tuple) and len(dr) == 2
        start, end = dr
        assert isinstance(start, date)
        assert isinstance(end, date)
