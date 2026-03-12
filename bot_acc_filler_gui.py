#!/usr/bin/env python3
"""
================================================================================
  BOT Accountant Excel Filler — Desktop GUI
  ──────────────────────────────────────────
  A modern, dark-themed drag-and-drop desktop application for filling
  exchange rates in accountant spreadsheets.

  Launch:
    python3 bot_acc_filler.py --gui
    python3 bot_acc_filler_gui.py
================================================================================
"""

import sys
import os
import asyncio
import threading
import subprocess
from datetime import datetime

# ─── Ensure local _libs is on path ───────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(SCRIPT_DIR, "_libs")
if not os.path.exists(_LIBS_DIR):
    os.makedirs(_LIBS_DIR)
if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

# ─── Auto-install customtkinter if not available ─────────────
try:
    import customtkinter as ctk  # type: ignore
except ImportError:
    print("  Installing required package 'customtkinter' locally...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "--target", _LIBS_DIR, "customtkinter",
        "--break-system-packages", "--quiet",
    ])
    import importlib
    importlib.invalidate_caches()
    import customtkinter as ctk  # type: ignore

import tkinter as tk
from tkinter import filedialog


# ═══════════════════════════════════════════════════════════════
# GUI APPLICATION
# ═══════════════════════════════════════════════════════════════

class FillerApp(ctk.CTk):
    """Main GUI window for the BOT Accountant Excel Filler."""

    def __init__(self):
        super().__init__()

        # ── Window configuration ──────────────────────────────
        self.title("BOT Accountant Excel Filler v2.0")
        self.geometry("720x620")
        self.minsize(600, 500)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.selected_file: str = ""
        self.output_file: str = ""
        self.is_running: bool = False

        self._build_ui()

    def _build_ui(self):
        """Construct all UI widgets."""

        # ── Header ────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 5))

        ctk.CTkLabel(
            header,
            text="🏦  BOT Exchange Rate Filler",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="v2.0",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(side="left", padx=(10, 0), pady=(6, 0))

        # ── Drop zone / file picker ──────────────────────────
        drop_frame = ctk.CTkFrame(self, corner_radius=12, border_width=2, border_color="#3B82F6")
        drop_frame.pack(fill="x", padx=20, pady=10)

        self.file_label = ctk.CTkLabel(
            drop_frame,
            text="📂  Click to select an Excel file (.xlsx)",
            font=ctk.CTkFont(size=14),
            text_color="#93C5FD",
            cursor="hand2",
        )
        self.file_label.pack(pady=30)
        self.file_label.bind("<Button-1>", self._pick_file)

        # ── Action buttons row ────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(5, 10))

        self.run_btn = ctk.CTkButton(
            btn_frame,
            text="▶  Fill Rates",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42,
            corner_radius=8,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self._start_fill,
            state="disabled",
        )
        self.run_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.open_btn = ctk.CTkButton(
            btn_frame,
            text="📁  Open Output",
            font=ctk.CTkFont(size=14),
            height=42,
            corner_radius=8,
            fg_color="#059669",
            hover_color="#047857",
            command=self._open_output,
            state="disabled",
        )
        self.open_btn.pack(side="right", expand=True, fill="x", padx=(5, 0))

        # ── Progress bar ──────────────────────────────────────
        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=6)
        self.progress.pack(fill="x", padx=20, pady=(0, 5))
        self.progress.set(0)

        # ── Log output area ──────────────────────────────────
        log_frame = ctk.CTkFrame(self, corner_radius=8)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        self.log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Menlo", size=12),
            corner_radius=8,
            state="disabled",
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Status bar ────────────────────────────────────────
        self.status_label = ctk.CTkLabel(
            self,
            text="Ready — select an Excel file to begin",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.status_label.pack(pady=(0, 10))

    # ── File picker ───────────────────────────────────────────
    def _pick_file(self, event=None):
        """Open a native file dialog to select an .xlsx file."""
        if self.is_running:
            return

        path = filedialog.askopenfilename(
            title="Select Accountant Excel File",
            filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")],
            initialdir=os.path.expanduser("~"),
        )
        if path:
            self.selected_file = path
            base = os.path.splitext(os.path.basename(path))[0]
            self.output_file = os.path.join(os.path.dirname(path), f"{base}_updated.xlsx")

            self.file_label.configure(
                text=f"📄  {os.path.basename(path)}",
                text_color="#60A5FA",
            )
            self.run_btn.configure(state="normal")
            self.open_btn.configure(state="disabled")
            self.status_label.configure(text=f"Selected: {os.path.basename(path)}")
            self._log_clear()

    # ── Run the filler in a background thread ────────────────
    def _start_fill(self):
        """Start the async filler in a background thread so the GUI stays responsive."""
        if self.is_running or not self.selected_file:
            return

        self.is_running = True
        self.run_btn.configure(state="disabled", text="⏳  Processing...")
        self.open_btn.configure(state="disabled")
        self.progress.start()
        self._log_clear()

        thread = threading.Thread(target=self._run_async_filler, daemon=True)
        thread.start()

    def _run_async_filler(self):
        """Worker thread: runs the async pipeline and updates the GUI upon completion."""
        try:
            from bot_acc_filler import run_filler

            # Create a thread-safe log function that updates the GUI
            def gui_log(msg):
                self.after(0, self._log_append, msg)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stats = loop.run_until_complete(
                run_filler(self.selected_file, self.output_file, gui_log)
            )
            loop.close()

            # Update GUI on completion
            self.after(0, self._on_complete, stats)

        except Exception as e:
            self.after(0, self._log_append, f"\n  ✗ Error: {e}")
            self.after(0, self._on_error)

    def _on_complete(self, stats):
        """Called on the main thread when processing finishes successfully."""
        self.is_running = False
        self.progress.stop()
        self.progress.set(1)
        self.run_btn.configure(state="normal", text="▶  Fill Rates")
        self.open_btn.configure(state="normal")
        self.status_label.configure(
            text=f"✓ Done — {stats.get('filled', 0)} rows filled, "
                 f"{stats.get('errors', 0)} errors"
        )

    def _on_error(self):
        """Called on the main thread when processing fails."""
        self.is_running = False
        self.progress.stop()
        self.progress.set(0)
        self.run_btn.configure(state="normal", text="▶  Fill Rates")
        self.status_label.configure(text="✗ An error occurred — see logs above")

    # ── Open the generated output file ───────────────────────
    def _open_output(self):
        """Open the output Excel file with the system default application."""
        if os.path.exists(self.output_file):
            if sys.platform == "darwin":
                subprocess.Popen(["open", self.output_file])
            elif sys.platform == "win32":
                os.startfile(self.output_file)  # type: ignore
            else:
                subprocess.Popen(["xdg-open", self.output_file])

    # ── Log helpers ───────────────────────────────────────────
    def _log_append(self, text: str):
        """Append a line to the log text box (thread-safe via after())."""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_clear(self):
        """Clear all text from the log box."""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


# ═══════════════════════════════════════════════════════════════
# ENTRY POINTS
# ═══════════════════════════════════════════════════════════════

def launch_gui():
    """Create and run the GUI application. Called by bot_acc_filler.py --gui."""
    app = FillerApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()


# ─── Changelog ───────────────────────────────────────────────
# 2026-03-12 | v1.0 — Initial GUI
#            | - Dark-themed CustomTkinter window with file picker
#            | - Background thread for async processing
#            | - Live progress bar and scrollable log output
#            | - "Open Output" button to view results
