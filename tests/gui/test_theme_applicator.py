#!/usr/bin/env python3
"""
tests/gui/test_theme_applicator.py
---------------------------------------------------------------------------
Behavior-class tests for gui/theme_applicator.apply_theme_to_app (F83).

Constructing a real BOTExrateApp would spawn a second Tk interpreter
alongside the session-scoped tk_root (segfaults CTk on macOS/aarch64), so the
orchestrator is driven over a real CTkFrame host carrying REAL CTk widgets
under every attribute name the applicator's registry inspects. This is the
same duck-typing contract apply_theme_to_app documents ("any CTk window with
the expected widget attributes").

Covered:
  * every registry entry recolors its widget to the ACTIVE theme token in
    both Dark and Light mode — including the widgets the pre-Wave-4
    applicator skipped entirely (_btn_settings/_btn_help, lbl_empty_state,
    btn_clear_queue, toggle_dryrun + hint, btn_backups, btn_export_exrate,
    btn_verify_rates);
  * a dark<->light switch actually CHANGES the painted color for every
    widget whose token differs between modes (a stale skip would keep the
    old mode's color);
  * the panel delegations (rate_ticker / console / scheduler_panel) receive
    the active theme dict.

All Tk tests require a display; the tk_root fixture skips on headless CI.
"""

from types import SimpleNamespace

import customtkinter as ctk
import pytest

from gui.theme import get_theme
from gui.theme_applicator import apply_theme_to_app

pytestmark = pytest.mark.gui


# Registry contract: app attribute -> (widget kind, configured property,
# theme token the applicator must paint it with).
SPEC = [
    ("hdr_frame", "frame", "fg_color", "header_bg"),
    ("lbl_header_title", "label", "text_color", "header_text"),
    ("lbl_header_sub", "label", "text_color", "header_sub"),
    ("_btn_settings", "button", "fg_color", "settings_btn"),
    ("_btn_help", "button", "fg_color", "settings_btn"),
    ("card", "frame", "fg_color", "card_bg"),
    ("lbl_date_section", "label", "text_color", "text_secondary"),
    ("dz_sub", "label", "text_color", "text_muted"),
    ("lbl_empty_state", "label", "text_color", "text_muted"),
    ("lbl_queue", "label", "text_color", "text_secondary"),
    ("btn_clear_queue", "button", "fg_color", "btn_secondary"),
    ("toggle_dryrun", "switch", "progress_color", "warning"),
    ("lbl_dryrun_hint", "label", "text_color", "text_muted"),
    ("progressbar", "progress", "progress_color", "trust_blue"),
    ("btn_process", "button", "fg_color", "trust_blue"),
    ("btn_revert", "button", "fg_color", "revert_bg"),
    ("btn_backups", "button", "fg_color", "btn_secondary"),
    ("btn_export_exrate", "button", "fg_color", "accent_indigo"),
    ("btn_verify_rates", "button", "fg_color", "btn_secondary"),
    ("btn_reveal", "button", "fg_color", "warning"),
    ("footer_frame", "frame", "fg_color", "footer_bg"),
    ("lbl_footer", "label", "text_color", "text_muted"),
]

# The widgets the pre-Wave-4 applicator did NOT recolor at all. These must
# now follow the active theme like everything else.
PREVIOUSLY_SKIPPED = (
    "_btn_settings",
    "_btn_help",
    "lbl_empty_state",
    "btn_clear_queue",
    "toggle_dryrun",
    "lbl_dryrun_hint",
    "btn_backups",
    "btn_export_exrate",
    "btn_verify_rates",
)


class _PanelRecorder:
    """Stand-in for rate_ticker / console / scheduler_panel delegation."""

    def __init__(self):
        self.applied = []

    def apply_theme(self, theme):
        self.applied.append(theme)


@pytest.fixture
def _restore_mode():
    original = ctk.get_appearance_mode()
    yield
    ctk.set_appearance_mode(original)


@pytest.fixture
def theme_host(tk_root):
    """A CTkFrame 'app' carrying a real widget for every registry entry."""
    host = ctk.CTkFrame(tk_root)
    makers = {
        "frame": lambda: ctk.CTkFrame(host),
        "label": lambda: ctk.CTkLabel(host, text="x"),
        "button": lambda: ctk.CTkButton(host, text="x"),
        "switch": lambda: ctk.CTkSwitch(host, text="x"),
        "progress": lambda: ctk.CTkProgressBar(host),
    }
    for attr, kind, _prop, _token in SPEC:
        setattr(host, attr, makers[kind]())
    host.rate_ticker = _PanelRecorder()
    host.console = _PanelRecorder()
    host.scheduler_panel = _PanelRecorder()
    yield host
    host.destroy()


@pytest.mark.parametrize("mode", ["Dark", "Light"])
def test_registry_paints_active_tokens(theme_host, _restore_mode, mode):
    """Every registered widget gets the ACTIVE mode's token — including the
    previously-skipped ones, which are part of SPEC and asserted here."""
    ctk.set_appearance_mode(mode)
    theme = get_theme()

    apply_theme_to_app(theme_host)

    mismatches = []
    for attr, _kind, prop, token in SPEC:
        actual = str(getattr(theme_host, attr).cget(prop))
        if actual != theme[token]:
            mismatches.append(f"{attr}.{prop}: {actual!r} != {token}")
    assert not mismatches, f"[{mode}] " + "; ".join(mismatches)


def test_dark_light_switch_recolors_every_mode_varying_widget(
    theme_host, _restore_mode
):
    """Switching dark<->light must repaint each widget whose token differs
    between modes; the previously-skipped widgets must be among them."""
    ctk.set_appearance_mode("Dark")
    dark = get_theme()
    apply_theme_to_app(theme_host)
    painted_dark = {
        attr: str(getattr(theme_host, attr).cget(prop))
        for attr, _kind, prop, _token in SPEC
    }

    ctk.set_appearance_mode("Light")
    light = get_theme()
    apply_theme_to_app(theme_host)

    varying = [
        (attr, prop, token)
        for attr, _kind, prop, token in SPEC
        if dark[token] != light[token]
    ]
    # The switch must be observable at all, and specifically on the widgets
    # the pre-Wave-4 applicator skipped.
    assert varying, "no theme token differs between Dark and Light"
    varying_attrs = {attr for attr, _prop, _token in varying}
    skipped_and_varying = varying_attrs & set(PREVIOUSLY_SKIPPED)
    assert skipped_and_varying, (
        "none of the previously-skipped widgets uses a mode-varying token"
    )

    stale = []
    for attr, prop, token in varying:
        now = str(getattr(theme_host, attr).cget(prop))
        if now == painted_dark[attr]:
            stale.append(attr)
        if now != light[token]:
            stale.append(f"{attr} (wrong token)")
    assert not stale, f"widgets kept their Dark colors after Light: {stale}"


@pytest.mark.parametrize("panel_attr", ["rate_ticker", "console", "scheduler_panel"])
def test_panels_receive_active_theme_dict(
    theme_host, _restore_mode, panel_attr
):
    ctk.set_appearance_mode("Light")
    theme = get_theme()
    apply_theme_to_app(theme_host)
    recorder = getattr(theme_host, panel_attr)
    assert recorder.applied, f"{panel_attr}.apply_theme was not called"
    assert recorder.applied[-1]["bg"] == theme["bg"]


def test_none_rate_ticker_is_tolerated(theme_host, _restore_mode):
    """app.rate_ticker is None when ticker init failed — must not raise."""
    theme_host.rate_ticker = None
    ctk.set_appearance_mode("Dark")
    apply_theme_to_app(theme_host)  # must not raise


def test_missing_widgets_are_skipped_not_fatal(tk_root, _restore_mode):
    """A bare host (no registered widgets) applies cleanly — the registry is
    hasattr-guarded so partial apps (tests, future panels) never crash."""
    host = ctk.CTkFrame(tk_root)
    bare = SimpleNamespace(configure=host.configure)
    apply_theme_to_app(bare)  # must not raise
    host.destroy()
