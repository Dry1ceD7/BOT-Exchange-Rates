#!/usr/bin/env python3
"""
gui/theme.py
---------------------------------------------------------------------------
Centralized theme system for the BOT Exchange Rate Processor.
Returns the full color palette based on the current appearance mode.
---------------------------------------------------------------------------
"""

import logging

import customtkinter as ctk

logger = logging.getLogger(__name__)


def get_theme() -> dict:
    """Return the active color palette based on customtkinter appearance mode.

    Dark mode: navy/slate backgrounds with light text.
    Light mode: white/gray backgrounds with dark text.
    """
    mode = ctk.get_appearance_mode()  # Returns "Dark", "Light", or "System"
    logger.debug("get_theme: ctk.get_appearance_mode() = %r", mode)

    # Determine if we should use dark palette
    if mode.lower() == "light":
        is_dark = False
    elif mode.lower() == "dark":
        is_dark = True
    else:
        # System mode — try to detect, default to dark on failure
        try:
            is_dark = ctk.AppearanceModeTracker.detect_appearance_mode() == 1
        except (AttributeError, RuntimeError):
            is_dark = True

    if is_dark:
        return {
            "bg":           "#0B1A33",
            "header_bg":    "#1A365D",
            "header_text":  "#FFFFFF",
            "header_sub":   "#94A3B8",
            "card_bg":      "#1E293B",
            "card_border":  "#334155",
            "divider":      "#334155",
            "section_bg":   "#0F172A",
            "text_primary": "#F1F5F9",
            "text_secondary": "#94A3B8",
            "text_muted":   "#64748B",
            "trust_blue":   "#3B82F6",
            "blue_hover":   "#2563EB",
            "success":      "#22C55E",
            "success_hover": "#16A34A",
            "warning":      "#F59E0B",
            "warning_hover": "#D97706",
            "revert_bg":    "#DC2626",
            "revert_hover": "#B91C1C",
            "error_text":   "#F87171",
            "process_text": "#60A5FA",
            "drop_border":  "#475569",
            "combo_bg":     "#1E293B",
            "combo_border": "#475569",
            "switch_track": "#475569",
            "switch_thumb": "#94A3B8",
        }
    else:
        return {
            "bg":           "#EEF2F7",
            "header_bg":    "#2D4A7A",
            "header_text":  "#FFFFFF",
            "header_sub":   "#CBD5E1",
            "card_bg":      "#FFFFFF",
            "card_border":  "#D1D9E6",
            "divider":      "#D1D9E6",
            "section_bg":   "#F5F7FA",
            "text_primary": "#1A202C",
            "text_secondary": "#4A5568",
            "text_muted":   "#A0AEC0",
            "trust_blue":   "#2B6CB0",
            "blue_hover":   "#2C5282",
            "success":      "#2F855A",
            "success_hover": "#276749",
            "warning":      "#C05621",
            "warning_hover": "#9C4221",
            "revert_bg":    "#C53030",
            "revert_hover": "#9B2C2C",
            "error_text":   "#C53030",
            "process_text": "#2B6CB0",
            "drop_border":  "#CBD5E1",
            "combo_bg":     "#F7FAFC",
            "combo_border": "#CBD5E1",
            "switch_track": "#CBD5E1",
            "switch_thumb": "#4A5568",
        }
