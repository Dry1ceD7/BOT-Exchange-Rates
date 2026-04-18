#!/usr/bin/env python3
"""
gui/panels/_path_chooser.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Path Removal Chooser (internal helper)
---------------------------------------------------------------------------
Tiny modal that lets the user pick which watch-folder to remove.
Internal to the scheduler panel — not intended for external use.

SFFB: < 60 lines.
"""

import os
from typing import List, Optional

import customtkinter as ctk

from gui.theme import get_theme


def choose_path_to_remove(parent, paths: List[str]) -> Optional[int]:
    """
    Show a modal with radio buttons for each path.
    Returns the index of the selected path, or None if cancelled.
    """
    t = get_theme()
    result = {"index": None}

    dialog = ctk.CTkToplevel(parent)
    dialog.title("Remove Watch Folder")
    dialog.geometry("360x220")
    dialog.resizable(False, False)
    dialog.configure(fg_color=t["modal_bg"])
    dialog.transient(parent.winfo_toplevel())
    dialog.grab_set()

    dialog.update_idletasks()
    sx = (dialog.winfo_screenwidth() - 360) // 2
    sy = (dialog.winfo_screenheight() - 220) // 2
    dialog.geometry(f"360x220+{sx}+{sy}")

    ctk.CTkLabel(
        dialog, text="Select folder to remove:",
        font=ctk.CTkFont(size=14, weight="bold"),
        text_color=t["modal_text"],
    ).pack(pady=(16, 8))

    selected = ctk.IntVar(value=0)
    for i, path in enumerate(paths):
        label = os.path.basename(path) or path
        ctk.CTkRadioButton(
            dialog, text=f"📁 {label}",
            variable=selected, value=i,
            font=ctk.CTkFont(size=12),
            text_color=t["modal_text"],
        ).pack(anchor="w", padx=24, pady=2)

    btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_row.pack(fill="x", padx=24, pady=(12, 16))

    def _cancel():
        dialog.destroy()

    def _confirm():
        result["index"] = selected.get()
        dialog.destroy()

    ctk.CTkButton(
        btn_row, text="Cancel",
        fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
        font=ctk.CTkFont(size=12), corner_radius=6,
        height=32, width=100, command=_cancel,
    ).pack(side="left")

    ctk.CTkButton(
        btn_row, text="Remove",
        fg_color=t["revert_bg"], hover_color=t["revert_hover"],
        font=ctk.CTkFont(size=12, weight="bold"), corner_radius=6,
        height=32, width=100, command=_confirm,
    ).pack(side="right")

    parent.wait_window(dialog)
    return result["index"]
