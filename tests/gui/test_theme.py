#!/usr/bin/env python3
"""
tests/gui/test_theme.py
---------------------------------------------------------------------------
GUI-lane checks for gui/theme.py within the real CustomTkinter runtime.

The pure-core contrast math lives in tests/test_theme_contrast.py (no display
needed). These tests run under the session-scoped withdrawn CTk root and:

  1. Confirm get_theme() resolves the contrast-safe muted tokens for the
     active appearance mode inside the live Tk runtime (not just as a bare
     dict import).
  2. Confirm a CTkLabel can actually be constructed with the muted token as
     its text_color over its real background token — exercising the exact
     consume pattern from app.py / scheduler_panel.py / rate_ticker.py.

All tests require a display; the tk_root fixture skips them on headless CI.
"""

import customtkinter as ctk
import pytest

from gui.theme import get_theme

pytestmark = pytest.mark.gui


@pytest.fixture
def _restore_mode():
    original = ctk.get_appearance_mode()
    yield
    ctk.set_appearance_mode(original)


def test_light_mode_muted_tokens_are_legible_values(tk_root, _restore_mode):
    """In the live runtime, light-mode muted tokens are the darkened values."""
    ctk.set_appearance_mode("Light")
    theme = get_theme()
    # The pre-fix illegible grays must be gone. The placeholder token is
    # tuned for the navy header_bg it actually renders on (was the dead
    # ticker_muted, which nothing consumed).
    assert theme["text_muted"] != "#A0AEC0"
    assert theme["ticker_placeholder"] == "#CBD5E1"


def test_dark_mode_muted_tokens_are_legible_values(tk_root, _restore_mode):
    """In the live runtime, dark-mode muted tokens are the darkened values."""
    ctk.set_appearance_mode("Dark")
    theme = get_theme()
    assert theme["text_muted"] != "#64748B"
    assert theme["ticker_placeholder"] == "#CBD5E1"


@pytest.mark.parametrize("mode", ["Light", "Dark"])
def test_muted_label_constructs_with_theme_colors(tk_root, mode, _restore_mode):
    """A CTkLabel paints the muted token over its real background token.

    Mirrors the dz_sub / footer / ticker-placeholder consume sites: a label
    using text_muted as text_color living inside a frame colored card_bg.
    """
    ctk.set_appearance_mode(mode)
    theme = get_theme()

    frame = ctk.CTkFrame(tk_root, fg_color=theme["card_bg"])
    label = ctk.CTkLabel(
        frame,
        text="or click to browse",
        text_color=theme["text_muted"],
    )
    label.pack()
    frame.pack()
    tk_root.update_idletasks()

    # CTk normalizes the configured color; the label must round-trip the
    # exact muted token we handed it for the active mode.
    assert str(label.cget("text_color")) == theme["text_muted"]

    label.destroy()
    frame.destroy()
