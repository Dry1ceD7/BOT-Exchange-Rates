#!/usr/bin/env python3
"""
gui/panels/live_console.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Live Processing Console Panel
---------------------------------------------------------------------------
Read-only CTkTextbox that tails the EventBus queue, providing terminal-like
feedback inside the GUI (e.g., "Fetching USD... Complete").

SFFB: Strict < 200 lines.
"""

import logging
from typing import Optional

import customtkinter as ctk

from core.workers.event_bus import EventBus

logger = logging.getLogger(__name__)

# Console color tokens
CONSOLE_BG = "#0F172A"
CONSOLE_TEXT = "#E2E8F0"
CONSOLE_ACCENT = "#38BDF8"
CONSOLE_ERROR = "#F87171"
CONSOLE_SUCCESS = "#4ADE80"


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
        super().__init__(
            master,
            fg_color=CONSOLE_BG,
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
            font=ctk.CTkFont(family="Courier", size=11, weight="bold"),
            text_color=CONSOLE_ACCENT,
        ).pack(anchor="w", padx=12, pady=(8, 0))

        # Log textbox
        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Courier", size=12),
            fg_color=CONSOLE_BG,
            text_color=CONSOLE_TEXT,
            wrap="word",
            activate_scrollbars=True,
            state="disabled",
        )
        self._textbox.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    def append_line(self, text: str, color: Optional[str] = None) -> None:
        """Append a single line to the console."""
        self._textbox.configure(state="normal")
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

    def _poll(self) -> None:
        """Drain events from the bus and render them."""
        if not self._polling:
            return
        events = self._bus.drain()
        for event in events:
            msg = event.get("msg", str(event))
            etype = event.get("type", "log")
            prefix = {
                "log": "[LOG]",
                "progress": "[...]",
                "error": "[ERR]",
                "success": "[OK ]",
            }.get(etype, "[---]")
            self.append_line(f"{prefix}  {msg}")
        self.after(self.POLL_INTERVAL_MS, self._poll)
