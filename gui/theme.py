#!/usr/bin/env python3
"""
gui/theme.py
---------------------------------------------------------------------------
Centralized theme system for the BOT Exchange Rate Processor.
Returns the full color palette based on the current appearance mode.
---------------------------------------------------------------------------
"""

import logging
import platform

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Cross-platform monospace font — single source of truth
MONO_FONT = "Menlo" if platform.system() == "Darwin" else (
    "Consolas" if platform.system() == "Windows" else "DejaVu Sans Mono"
)


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
            "switch_hover": "#475569",
            "footer_bg":    "#0C111D",
            "settings_btn":       "#334155",
            "settings_btn_hover": "#475569",
            "settings_btn_text":  "#E2E8F0",
            "settings_btn_border": "#475569",
            # ── Console (terminal) ──────────────────────────
            "console_bg":      "#0F172A",
            "console_text":    "#E2E8F0",
            "console_accent":  "#38BDF8",
            "console_error":   "#F87171",
            "console_success": "#4ADE80",
            # ── Modal / Dialog ──────────────────────────────
            "modal_bg":        "#1E293B",
            "modal_text":      "#F1F5F9",
            "modal_muted":     "#94A3B8",
            "modal_accent":    "#3B82F6",
            "modal_success":   "#22C55E",
            "modal_entry_bg":  "#334155",
            # ── Scheduler panel ─────────────────────────────
            "sched_bg":        "#162032",
            "sched_border":    "#2D3E55",
            # ── Accent (indigo for ExRate / feature) ────────
            "accent_indigo":   "#6366F1",
            "accent_indigo_hover": "#4F46E5",
            # ── Ticker ──────────────────────────────────────
            "ticker_value":    "#E2E8F0",
            "ticker_label":    "#94A3B8",
            "ticker_live":     "#ef4444",
            # ── Buttons (secondary) ───────────────────────────────
            "btn_secondary":       "#475569",
            "btn_secondary_hover": "#64748B",
            # ── Accent teal (CSV panel) ───────────────────────
            "accent_teal":       "#0F766E",
            "accent_teal_hover": "#115E59",
            # ── Scheduler option menu ────────────────────────
            "option_bg":          "#2D3E55",
            "path_list_bg":       "#0F172A",
            # ── Rate ticker trend ─────────────────────────────
            "ticker_up":          "#22C55E",
            "ticker_down":        "#EF4444",
            "ticker_neutral":     "#3B82F6",
            "ticker_muted":       "#64748B",
            "ticker_live_alt":    "#dc2626",
            # ── Update banner states ─────────────────────────
            "banner_warn":          "#F59E0B",
            "banner_warn_text":     "#1E293B",
            "banner_warn_hover":    "#D97706",
            "banner_dark":          "#1E293B",
            "banner_dark_hover":    "#0F172A",
            "banner_confirm_bg":    "#1E40AF",
            "banner_confirm_hover": "#1E3A8A",
            "banner_install":       "#2563EB",
            "banner_install_hover": "#3B82F6",
            "banner_apply":         "#059669",
            "banner_apply_hover":   "#10B981",
            "banner_error":         "#DC2626",
            "banner_success":       "#059669",
            "banner_success_btn":   "#FFFFFF",
            "banner_success_btn_h": "#D1FAE5",
            "banner_success_text":  "#065F46",
            "banner_later_hover":   "#047857",
            "banner_text_light":    "#FFFFFF",
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
            "switch_hover": "#A0AEC0",
            "footer_bg":    "#E2E8F0",
            "settings_btn":       "#E2E8F0",
            "settings_btn_hover": "#CBD5E1",
            "settings_btn_text":  "#1A202C",
            "settings_btn_border": "#CBD5E1",
            # ── Console (terminal) — stays dark in light mode ─
            "console_bg":      "#0F172A",
            "console_text":    "#E2E8F0",
            "console_accent":  "#38BDF8",
            "console_error":   "#F87171",
            "console_success": "#4ADE80",
            # ── Modal / Dialog ──────────────────────────────
            "modal_bg":        "#F5F7FA",
            "modal_text":      "#1A202C",
            "modal_muted":     "#4A5568",
            "modal_accent":    "#2B6CB0",
            "modal_success":   "#2F855A",
            "modal_entry_bg":  "#FFFFFF",
            # ── Scheduler panel ─────────────────────────────
            "sched_bg":        "#F0F4F8",
            "sched_border":    "#D1D9E6",
            # ── Accent (indigo for ExRate / feature) ────────
            "accent_indigo":   "#6366F1",
            "accent_indigo_hover": "#4F46E5",
            # ── Ticker ──────────────────────────────────────
            "ticker_value":    "#1A202C",
            "ticker_label":    "#4A5568",
            "ticker_live":     "#DC2626",
            # ── Buttons (secondary) ───────────────────────────
            "btn_secondary":       "#64748B",
            "btn_secondary_hover": "#475569",
            # ── Accent teal (CSV panel) ───────────────────────
            "accent_teal":       "#0D9488",
            "accent_teal_hover": "#0F766E",
            # ── Scheduler option menu ────────────────────────
            "option_bg":          "#E2E8F0",
            "path_list_bg":       "#FFFFFF",
            # ── Rate ticker trend ─────────────────────────────
            "ticker_up":          "#16A34A",
            "ticker_down":        "#DC2626",
            "ticker_neutral":     "#2563EB",
            "ticker_muted":       "#94A3B8",
            "ticker_live_alt":    "#B91C1C",
            # ── Update banner states ─────────────────────────
            "banner_warn":          "#F59E0B",
            "banner_warn_text":     "#1E293B",
            "banner_warn_hover":    "#D97706",
            "banner_dark":          "#1E293B",
            "banner_dark_hover":    "#0F172A",
            "banner_confirm_bg":    "#1E40AF",
            "banner_confirm_hover": "#1E3A8A",
            "banner_install":       "#2563EB",
            "banner_install_hover": "#3B82F6",
            "banner_apply":         "#059669",
            "banner_apply_hover":   "#10B981",
            "banner_error":         "#DC2626",
            "banner_success":       "#059669",
            "banner_success_btn":   "#FFFFFF",
            "banner_success_btn_h": "#D1FAE5",
            "banner_success_text":  "#065F46",
            "banner_later_hover":   "#047857",
            "banner_text_light":    "#FFFFFF",
        }
