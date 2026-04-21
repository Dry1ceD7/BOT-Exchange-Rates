#!/usr/bin/env python3
"""
gui/panels/live_console.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Live Processing Console Panel
---------------------------------------------------------------------------
Read-only CTkTextbox that tails the EventBus queue, providing terminal-like
feedback inside the GUI (e.g., "Fetching USD... Complete").

Color-tagged rendering: errors appear red, successes green, progress blue.

SFFB: Strict < 200 lines.
"""

import logging
from typing import Optional

import customtkinter as ctk

from core.workers.event_bus import EventBus
from gui.theme import MONO_FONT, get_theme

logger = logging.getLogger(__name__)


class LiveConsolePanel(ctk.CTkFrame):
    """
    A read-only, dark-themed log viewer that polls an EventBus.

    Usage:
        bus = EventBus()
        console = LiveConsolePanel(parent, event_bus=bus)
        console.pack(fill="x")
        console.start_polling()  # begins root.after() loop
    """

    POLL_INTERVAL_MS = 100  # 10 FPS drain rate

    def __init__(
        self,
        master,
        event_bus: Optional[EventBus] = None,
        height: int = 160,
        **kwargs,
    ):
        t = get_theme()
        super().__init__(
            master,
            fg_color=t["console_bg"],
            corner_radius=10,
            height=height,
            **kwargs,
        )
        self.pack_propagate(False)

        self._bus = event_bus or EventBus()
        self._polling = False

        # Header
        ctk.CTkLabel(
            self,
            text="PROCESSING LOG",
            font=ctk.CTkFont(family=MONO_FONT, size=11, weight="bold"),
            text_color=t["console_accent"],
        ).pack(anchor="w", padx=12, pady=(8, 0))

        # Log textbox
        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            fg_color=t["console_bg"],
            text_color=t["console_text"],
            wrap="word",
            activate_scrollbars=True,
            state="disabled",
        )
        self._textbox.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        # ── Color tags for structured log rendering ──────────────────────
        self._setup_color_tags()

    def _setup_color_tags(self) -> None:
        """Configure Tkinter text tags for color-coded log lines."""
        t = get_theme()
        tb = self._textbox._textbox  # access underlying Tk Text widget
        tb.tag_configure("error",   foreground=t["console_error"])
        tb.tag_configure("success", foreground=t["console_success"])
        tb.tag_configure("accent",  foreground=t["console_accent"])
        tb.tag_configure("log",     foreground=t["console_text"])
        tb.tag_configure("warning", foreground=t.get("warning", "#F59E0B"))

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    def append_line(self, text: str, tag: Optional[str] = None) -> None:
        """Append a single line to the console with optional color tag."""
        self._textbox.configure(state="normal")
        if tag:
            # Insert with color tag via underlying Tk Text widget
            tb = self._textbox._textbox
            tb.insert("end", text + "\n", tag)
        else:
            self._textbox.insert("end", text + "\n")
        self._textbox.configure(state="disabled")
        self._textbox.see("end")

    def clear(self) -> None:
        """Clear all console content."""
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")

    def start_polling(self) -> None:
        """Begin the root.after() polling loop to drain the EventBus."""
        self._polling = True
        self._poll()

    def stop_polling(self) -> None:
        """Stop the polling loop."""
        self._polling = False

    def apply_theme(self, t: dict) -> None:
        """Re-apply theme colors to console frame and text tags."""
        self.configure(fg_color=t["console_bg"])
        self._textbox.configure(
            fg_color=t["console_bg"],
            text_color=t["console_text"],
        )
        self._setup_color_tags()

    def _poll(self) -> None:
        """Drain events from the bus and render them with color tags."""
        if not self._polling:
            return
        events = self._bus.drain()
        if events:
            # Batch: unlock once, insert all, lock once
            self._textbox.configure(state="normal")
            tb = self._textbox._textbox
            for event in events:
                msg = event.get("msg", str(event))
                etype = event.get("type", "log")
                prefix_map = {
                    "log":      ("[LOG]", "log"),
                    "progress": ("[...]", "accent"),
                    "error":    ("[ERR]", "error"),
                    "success":  ("[OK ]", "success"),
                    "warning":  ("[WRN]", "warning"),
                }
                prefix, tag = prefix_map.get(etype, ("[---]", "log"))
                tb.insert("end", f"{prefix}  {msg}\n", tag)
            self._textbox.configure(state="disabled")
            self._textbox.see("end")
        self.after(self.POLL_INTERVAL_MS, self._poll)
