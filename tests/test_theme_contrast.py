#!/usr/bin/env python3
"""
tests/test_theme_contrast.py
---------------------------------------------------------------------------
WCAG 2.x contrast regression tests for gui/theme.py.

Secondary / hint / muted text tokens must clear the WCAG AA normal-text
contrast floor of 4.5:1 against the backgrounds they are actually painted
on. A 2026-06-04 audit found Light-mode `text_muted` (#A0AEC0) scoring only
~1.8-2.3:1 on the white card, footer, scheduler and section backgrounds, and
Dark-mode `text_muted` (#64748B) scoring 3.07:1 on card_bg — both well below
the floor, leaving sub-labels ("or click to browse"), the dry-run hint, the
footer and ticker placeholders nearly illegible.

These tests pin the contrast contract so a future palette tweak cannot
silently re-introduce an illegible token.  No display / CTk root is required:
get_theme() resolves its palette from ctk.set_appearance_mode() alone.
"""

import customtkinter as ctk
import pytest

from gui.theme import get_theme

# WCAG AA contrast floor for normal-size text.
WCAG_AA_NORMAL = 4.5


# ---------------------------------------------------------------------------
# Contrast math (WCAG 2.x relative luminance)
# ---------------------------------------------------------------------------

def _channel(value: int) -> float:
    """Linearize one 8-bit sRGB channel per the WCAG definition."""
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colors (always >= 1.0)."""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def _restore_mode():
    """Save/restore the process-wide CTk appearance mode around a test."""
    original = ctk.get_appearance_mode()
    yield
    ctk.set_appearance_mode(original)


def _theme(mode: str) -> dict:
    ctk.set_appearance_mode(mode)
    return get_theme()


# ---------------------------------------------------------------------------
# (token, background-token) pairs that must clear the AA floor.
#
# Each pair reflects how the token is actually consumed in the GUI:
#   text_muted  -> dz_sub on card_bg, dry-run hint on card/section, footer on
#                  footer_bg, scheduler hints on sched_bg.
#   ticker_muted -> placeholder "--.--" on card_bg.
#   modal_muted / text_secondary / header_sub -> dialog & header sub-labels.
# We test each muted token against EVERY real background it can land on, so a
# pass means it is legible everywhere, not just on the lightest surface.
# ---------------------------------------------------------------------------
_MUTED_PAIRS = [
    ("text_muted", "card_bg"),
    ("text_muted", "section_bg"),
    ("text_muted", "footer_bg"),
    ("text_muted", "sched_bg"),
    ("text_muted", "bg"),
    ("text_secondary", "card_bg"),
    ("text_secondary", "section_bg"),
    ("ticker_muted", "card_bg"),
    ("ticker_label", "card_bg"),
    ("modal_muted", "modal_bg"),
    ("header_sub", "header_bg"),
]


@pytest.mark.parametrize("mode", ["Light", "Dark"])
@pytest.mark.parametrize(("fg_key", "bg_key"), _MUTED_PAIRS)
def test_muted_tokens_meet_wcag_aa(mode, fg_key, bg_key, _restore_mode):
    """Every muted/secondary/hint token clears 4.5:1 on its real background."""
    theme = _theme(mode)
    ratio = contrast_ratio(theme[fg_key], theme[bg_key])
    assert ratio >= WCAG_AA_NORMAL, (
        f"{mode} mode: {fg_key} ({theme[fg_key]}) on {bg_key} "
        f"({theme[bg_key]}) = {ratio:.2f}:1, below WCAG AA floor "
        f"{WCAG_AA_NORMAL}:1"
    )


def test_light_text_muted_was_darkened(_restore_mode):
    """Regression: the old illegible light-mode #A0AEC0 must not return."""
    theme = _theme("Light")
    assert theme["text_muted"] != "#A0AEC0"
    # On the lightest surface (white card) it must clear AA comfortably.
    assert contrast_ratio(theme["text_muted"], theme["card_bg"]) >= WCAG_AA_NORMAL
    # And on the darkest surface it actually lands on (the footer bar).
    assert contrast_ratio(theme["text_muted"], theme["footer_bg"]) >= WCAG_AA_NORMAL


def test_dark_text_muted_was_darkened(_restore_mode):
    """Regression: the old failing dark-mode #64748B must not return."""
    theme = _theme("Dark")
    assert theme["text_muted"] != "#64748B"
    assert contrast_ratio(theme["text_muted"], theme["card_bg"]) >= WCAG_AA_NORMAL


def test_muted_stays_distinguishable_from_primary(_restore_mode):
    """Muted text must remain visibly lighter/grayer than primary body text.

    Darkening the muted token to hit AA should not collapse it into the
    primary text color, which would erase the visual hierarchy the token
    exists to provide.
    """
    for mode in ("Light", "Dark"):
        theme = _theme(mode)
        assert theme["text_muted"] != theme["text_primary"], (
            f"{mode}: text_muted collapsed into text_primary"
        )


def test_contrast_ratio_helper_sanity():
    """Guard the contrast helper itself against silent regression."""
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.1)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)
    # Symmetric in its arguments.
    assert contrast_ratio("#A0AEC0", "#FFFFFF") == pytest.approx(
        contrast_ratio("#FFFFFF", "#A0AEC0")
    )
