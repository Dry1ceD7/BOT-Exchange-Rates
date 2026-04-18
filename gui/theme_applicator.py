#!/usr/bin/env python3
"""
gui/theme_applicator.py
---------------------------------------------------------------------------
Extracted from gui/app.py (H-01 decomposition) — applies theme colors
to all GUI widgets without the app class needing to know every widget.
---------------------------------------------------------------------------
"""

import logging

import customtkinter as ctk

from gui.theme import get_theme

logger = logging.getLogger(__name__)


def apply_theme_to_app(app) -> None:
    """Re-read the theme and apply colors to ALL widgets on the app.

    Args:
        app: The BOTExrateApp instance (or any CTk window with the
             expected widget attributes).
    """
    t = get_theme()

    # ── Window background ─────────────────────────────────────────
    app.configure(fg_color=t["bg"])

    # ── Header ────────────────────────────────────────────────────
    if hasattr(app, "hdr_frame"):
        app.hdr_frame.configure(fg_color=t["header_bg"])
    if hasattr(app, "lbl_header_title"):
        app.lbl_header_title.configure(text_color=t["header_text"])
    if hasattr(app, "lbl_header_sub"):
        app.lbl_header_sub.configure(text_color=t["header_sub"])

    # ── Card ──────────────────────────────────────────────────────
    if hasattr(app, "card"):
        app.card.configure(
            fg_color=t["card_bg"],
            border_color=t["card_border"],
        )

    # ── Section title labels ──────────────────────────────────────
    for attr in ("lbl_date_section", "lbl_input_section"):
        widget = getattr(app, attr, None)
        if widget:
            widget.configure(text_color=t["text_secondary"])

    # ── Auto-detect toggle ────────────────────────────────────────
    if hasattr(app, "toggle_auto"):
        app.toggle_auto.configure(
            text_color=t["text_primary"],
            fg_color=t["switch_track"],
            button_color=t["switch_thumb"],
            button_hover_color=t["text_secondary"],
            progress_color=t["trust_blue"],
        )

    # ── Manual "Use Today" toggle ─────────────────────────────────
    if hasattr(app, "toggle_today"):
        app.toggle_today.configure(
            text_color=t["text_secondary"],
            fg_color=t["switch_track"],
            button_color=t["switch_thumb"],
        )

    # ── Date combo boxes ──────────────────────────────────────────
    if hasattr(app, "_combo_widgets"):
        for combo in app._combo_widgets:
            combo.configure(
                fg_color=t["combo_bg"],
                border_color=t["combo_border"],
                text_color=t["text_primary"],
                dropdown_fg_color=t["card_bg"],
                button_color=t["trust_blue"],
                button_hover_color=t["blue_hover"],
            )
    # Date combo labels (Year, Month, Day)
    if hasattr(app, "manual_date_frame"):
        for child in app.manual_date_frame.winfo_children():
            for sub in child.winfo_children():
                for label in sub.winfo_children():
                    if isinstance(label, ctk.CTkLabel):
                        label.configure(text_color=t["text_secondary"])

    # ── Drop zone ────────────────────────────────────────────────
    if hasattr(app, "drop_zone"):
        app.drop_zone.configure(
            fg_color=t["section_bg"],
            border_color=t["drop_border"],
        )
    if hasattr(app, "dz_text"):
        app.dz_text.configure(text_color=t["text_secondary"])
    if hasattr(app, "dz_sub"):
        app.dz_sub.configure(text_color=t["text_muted"])

    # ── Queue label ───────────────────────────────────────────────
    if hasattr(app, "lbl_queue"):
        app.lbl_queue.configure(text_color=t["text_secondary"])

    # ── Status box ────────────────────────────────────────────────
    if hasattr(app, "lbl_status"):
        status_parent = app.lbl_status.master
        if status_parent:
            status_parent.configure(
                fg_color=t["section_bg"],
                border_color=t["card_border"],
            )

    # ── Progress bar ──────────────────────────────────────────────
    if hasattr(app, "progressbar"):
        app.progressbar.configure(progress_color=t["trust_blue"])

    # ── Buttons ───────────────────────────────────────────────────
    if hasattr(app, "btn_process"):
        app.btn_process.configure(
            fg_color=t["trust_blue"],
            hover_color=t["blue_hover"],
        )
    if hasattr(app, "btn_revert"):
        app.btn_revert.configure(
            fg_color=t["revert_bg"],
            hover_color=t["revert_hover"],
        )
    if hasattr(app, "btn_reveal"):
        app.btn_reveal.configure(
            fg_color=t["warning"],
            hover_color=t["warning_hover"],
        )

    # ── Dividers — recolor all 1px height frames in card ─────────
    if hasattr(app, "card"):
        for child in app.card.winfo_children():
            try:
                if child.cget("height") == 1:
                    child.configure(fg_color=t["divider"])
            except (RuntimeError, AttributeError):
                pass

    # ── Live console keeps its dark terminal aesthetic ────────────
    # (intentionally not themed — it stays dark in both modes)

    # ── Rate Ticker ───────────────────────────────────────────────
    if hasattr(app, "rate_ticker") and app.rate_ticker is not None:
        app.rate_ticker.apply_theme(t)

    # ── Live Console ─────────────────────────────────────────────
    if hasattr(app, "console") and hasattr(app.console, "apply_theme"):
        app.console.apply_theme(t)

    # ── Scheduler panel ──────────────────────────────────────────
    if hasattr(app, "scheduler_panel") and hasattr(app.scheduler_panel, "apply_theme"):
        app.scheduler_panel.apply_theme(t)

    # ── Footer ──────────────────────────────────────────────────
    if hasattr(app, "footer_frame"):
        app.footer_frame.configure(fg_color=t["header_bg"])
    if hasattr(app, "lbl_footer"):
        app.lbl_footer.configure(text_color=t["header_sub"])

    logger.debug("Theme applied: %s mode", ctk.get_appearance_mode())
