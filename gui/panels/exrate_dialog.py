#!/usr/bin/env python3
"""
gui/panels/exrate_dialog.py
---------------------------------------------------------------------------
ExRate Sheet creation dialog — standalone TopLevel window.
Extracted from gui/app.py to reduce God Object line count.
---------------------------------------------------------------------------
"""

import asyncio
import logging
import os
import threading
from datetime import date
from tkinter import filedialog

import customtkinter as ctk
import httpx

from gui.theme import get_theme

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
EXRATE_CURRENCIES = [
    "USD", "EUR", "GBP", "JPY", "CNY", "HKD", "SGD", "AUD", "CHF",
]

EXRATE_RATE_TYPES = {
    "Buying TT":    "buying_transfer",
    "Buying Sight": "buying_sight",
    "Selling":      "selling",
    "Mid Rate":     "mid_rate",
}


def show_exrate_dialog(app) -> None:
    """Show the ExRate creation options dialog. Calls back into *app* for
    status/progress updates.

    Args:
        app: The BOTExrateApp instance (parent window).
    """
    t = get_theme()

    dialog = ctk.CTkToplevel(app)
    dialog.title("Create ExRate File")
    dialog.geometry("440x680")
    dialog.resizable(False, False)
    dialog.configure(fg_color=t["card_bg"])
    dialog.transient(app)
    dialog.grab_set()

    dialog.update_idletasks()
    sx = (dialog.winfo_screenwidth() - 440) // 2
    sy = (dialog.winfo_screenheight() - 680) // 2
    dialog.geometry(f"440x680+{sx}+{sy}")

    ctk.CTkLabel(
        dialog, text="ExRate Sheet Options",
        font=ctk.CTkFont(size=18, weight="bold"),
        text_color=t["text_primary"],
    ).pack(pady=(16, 12))

    # ── Currencies ────────────────────────────────────────────────────
    ctk.CTkLabel(
        dialog, text="Currencies",
        font=ctk.CTkFont(size=14, weight="bold"),
        text_color=t["text_secondary"],
    ).pack(anchor="w", padx=24, pady=(0, 4))

    cur_frame = ctk.CTkFrame(
        dialog, fg_color=t["section_bg"], corner_radius=8,
    )
    cur_frame.pack(fill="x", padx=24, pady=(0, 12))

    cur_vars = {}
    DEFAULTS_ON = {"USD", "EUR"}
    row_frame = None
    for i, ccy in enumerate(EXRATE_CURRENCIES):
        if i % 3 == 0:
            row_frame = ctk.CTkFrame(cur_frame, fg_color="transparent")
            row_frame.pack(fill="x", padx=8, pady=2)
        var = ctk.BooleanVar(value=ccy in DEFAULTS_ON)
        cur_vars[ccy] = var
        ctk.CTkCheckBox(
            row_frame, text=ccy, variable=var,
            font=ctk.CTkFont(size=13),
            text_color=t["text_primary"],
            fg_color=t["accent_indigo"], hover_color=t["accent_indigo_hover"],
            width=120,
        ).pack(side="left", padx=4, pady=2)

    # ── Rate Types ────────────────────────────────────────────────────
    ctk.CTkLabel(
        dialog, text="Rate Types",
        font=ctk.CTkFont(size=14, weight="bold"),
        text_color=t["text_secondary"],
    ).pack(anchor="w", padx=24, pady=(0, 4))

    rate_frame = ctk.CTkFrame(
        dialog, fg_color=t["section_bg"], corner_radius=8,
    )
    rate_frame.pack(fill="x", padx=24, pady=(0, 16))

    rate_vars = {}
    RATE_DEFAULTS = {"Buying TT", "Selling"}
    row_frame2 = None
    for i, (label, _) in enumerate(EXRATE_RATE_TYPES.items()):
        if i % 2 == 0:
            row_frame2 = ctk.CTkFrame(rate_frame, fg_color="transparent")
            row_frame2.pack(fill="x", padx=8, pady=2)
        var = ctk.BooleanVar(value=label in RATE_DEFAULTS)
        rate_vars[label] = var
        ctk.CTkCheckBox(
            row_frame2, text=label, variable=var,
            font=ctk.CTkFont(size=13),
            text_color=t["text_primary"],
            fg_color=t["accent_indigo"], hover_color=t["accent_indigo_hover"],
            width=180,
        ).pack(side="left", padx=4, pady=2)

    # ── Date Range ────────────────────────────────────────────────────
    ctk.CTkLabel(
        dialog, text="Date Range",
        font=ctk.CTkFont(size=14, weight="bold"),
        text_color=t["text_secondary"],
    ).pack(anchor="w", padx=24, pady=(0, 4))

    date_range_frame = ctk.CTkFrame(
        dialog, fg_color=t["section_bg"], corner_radius=8,
    )
    date_range_frame.pack(fill="x", padx=24, pady=(0, 16))

    date_mode_var = ctk.StringVar(value="auto")
    today = date.today()

    auto_label = ctk.CTkLabel(
        date_range_frame,
        text=f"  Auto: {today.year}-01-01 → {today.strftime('%Y-%m-%d')}",
        font=ctk.CTkFont(size=12),
        text_color=t["text_secondary"],
    )

    ctk.CTkSwitch(
        date_range_frame,
        text="  Select dates manually",
        variable=date_mode_var,
        onvalue="manual", offvalue="auto",
        font=ctk.CTkFont(size=13),
        text_color=t["text_primary"],
        progress_color=t["accent_indigo"],
        command=lambda: _toggle_date_mode(),
    ).pack(anchor="w", padx=12, pady=(8, 0))

    auto_label.pack(anchor="w", padx=12, pady=(2, 8))

    # Manual date inputs (initially hidden)
    manual_frame = ctk.CTkFrame(date_range_frame, fg_color="transparent")
    years = [str(y) for y in range(today.year - 5, today.year + 2)]
    months = [f"{m:02d}" for m in range(1, 13)]
    days = [f"{d:02d}" for d in range(1, 32)]

    # Start date row
    start_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
    start_row.pack(fill="x", padx=8, pady=2)
    ctk.CTkLabel(start_row, text="Start:", font=ctk.CTkFont(size=12),
                 text_color=t["text_secondary"], width=40).pack(side="left")
    start_year = ctk.CTkComboBox(
        start_row, values=years, width=80,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    start_year.set(str(today.year))
    start_year.pack(side="left", padx=2)
    start_month = ctk.CTkComboBox(
        start_row, values=months, width=60,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    start_month.set("01")
    start_month.pack(side="left", padx=2)
    start_day = ctk.CTkComboBox(
        start_row, values=days, width=60,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    start_day.set("01")
    start_day.pack(side="left", padx=2)

    # End date row
    end_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
    end_row.pack(fill="x", padx=8, pady=2)
    ctk.CTkLabel(end_row, text="End:", font=ctk.CTkFont(size=12),
                 text_color=t["text_secondary"], width=40).pack(side="left")
    end_year = ctk.CTkComboBox(
        end_row, values=years, width=80,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    end_year.set(str(today.year))
    end_year.pack(side="left", padx=2)
    end_month = ctk.CTkComboBox(
        end_row, values=months, width=60,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    end_month.set(f"{today.month:02d}")
    end_month.pack(side="left", padx=2)
    end_day = ctk.CTkComboBox(
        end_row, values=days, width=60,
        font=ctk.CTkFont(size=12),
        fg_color=t["combo_bg"], border_color=t["combo_border"],
        text_color=t["text_primary"],
    )
    end_day.set(f"{today.day:02d}")
    end_day.pack(side="left", padx=2)

    def _toggle_date_mode():
        if date_mode_var.get() == "manual":
            auto_label.pack_forget()
            manual_frame.pack(fill="x", pady=(0, 8))
        else:
            manual_frame.pack_forget()
            auto_label.pack(anchor="w", padx=12, pady=(2, 8))

    # ── Validation error label (reused, not stacked) ────────────────────
    _err_label = ctk.CTkLabel(
        dialog, text="",
        text_color=t["error_text"],
        font=ctk.CTkFont(size=12),
    )
    _err_label.pack(pady=(0, 4))

    # ── Create Button ─────────────────────────────────────────────────
    def _on_create():
        _err_label.configure(text="")  # clear previous error

        currencies = [c for c, v in cur_vars.items() if v.get()]
        rate_types = {
            lbl: EXRATE_RATE_TYPES[lbl]
            for lbl, v in rate_vars.items() if v.get()
        }
        if not currencies:
            _err_label.configure(text="Select at least one currency")
            return
        if not rate_types:
            _err_label.configure(text="Select at least one rate type")
            return

        # Get date range
        if date_mode_var.get() == "manual":
            try:
                s_date = date(int(start_year.get()), int(start_month.get()),
                              int(start_day.get()))
                e_date = date(int(end_year.get()), int(end_month.get()),
                              int(end_day.get()))
            except ValueError:
                _err_label.configure(text="Invalid date entered")
                return
            date_range = (s_date, e_date)
        else:
            date_range = None  # auto = current year

        dialog.destroy()
        _create_exrate_file(app, currencies, rate_types, date_range=date_range)

    ctk.CTkButton(
        dialog, text="Create ExRate File",
        fg_color=t["accent_indigo"], hover_color=t["accent_indigo_hover"],
        font=ctk.CTkFont(size=14, weight="bold"),
        corner_radius=10, height=44,
        command=_on_create,
    ).pack(padx=24, fill="x", pady=(0, 12))


def _create_exrate_file(app, currencies, rate_types, date_range=None):
    """Create a new standalone ExRate file — fully independent, pulls from BOT API.

    Uses a callback interface to communicate with the parent window.
    The `app` parameter is only used for:
      - app.after() (thread-safe scheduling)
      - app.event_bus (for LedgerEngine progress)
    All UI updates go through callbacks registered on `app` via the
    _get_ui_callbacks() helper.

    Args:
        app: The BOTExrateApp instance for scheduling + event bus.
        currencies: List of currency codes.
        rate_types: Dict of {label: api_key}.
        date_range: Optional (start_date, end_date) tuple.
    """
    t = get_theme()

    dest = filedialog.asksaveasfilename(
        title="Save ExRate File",
        initialfile="ExRate.xlsx",
        filetypes=[("Excel files", "*.xlsx")],
        defaultextension=".xlsx",
    )
    if not dest:
        return

    # ── Callback interface — decoupled from widget internals ─────────
    def _set_export_button(state: str):
        if hasattr(app, "btn_export_exrate"):
            app.btn_export_exrate.configure(state=state)

    def _set_status(text: str, color: str):
        if hasattr(app, "lbl_status"):
            app.lbl_status.configure(text=text, text_color=color)

    def _set_progress(mode: str = "determinate", value: float = 0, running: bool = False):
        if hasattr(app, "progressbar"):
            if mode:
                app.progressbar.configure(mode=mode)
            if running:
                app.progressbar.start()
            else:
                app.progressbar.stop()
                app.progressbar.set(value)

    def _on_complete(dest_path: str):
        if hasattr(app, "last_processed_path"):
            app.last_processed_path = dest_path
        if hasattr(app, "btn_reveal"):
            app.btn_reveal.pack(pady=(12, 14))

    # ── Start: disable button + indeterminate progress ───────────────
    _set_export_button("disabled")
    _set_status("Creating ExRate file...", t["text_secondary"])
    _set_progress("indeterminate", running=True)
    app.update_idletasks()

    def _status_cb(msg: str):
        try:
            app.after(0, _set_status, msg, t["text_secondary"])
        except RuntimeError:
            pass  # app destroyed during ExRate generation

    def _done(success: bool, message: str):
        """Main-thread callback to restore UI state."""
        _set_progress("determinate", value=1.0 if success else 0.0)
        if success:
            _set_status(message, t["success"])
            _on_complete(dest)
        else:
            _set_status(message, t["error_text"])
        _set_export_button("normal")

    event_bus = getattr(app, "event_bus", None)

    def _worker():
        from openpyxl import Workbook

        from core.api_client import CLIENT_TIMEOUT, BOTClient
        from core.engine import LedgerEngine

        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "ExRate"
            wb.save(dest)
            wb.close()

            async def _run():
                async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                    api = BOTClient(client)
                    engine = LedgerEngine(api, event_bus=event_bus)
                    return await engine.update_exrate_standalone(
                        dest,
                        progress_cb=_status_cb,
                        currencies=currencies,
                        rate_types=rate_types,
                        date_range=date_range,
                    )

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
                try:
                    app.after(0, _done, True,
                              f"✓ ExRate created: {os.path.basename(dest)}")
                except RuntimeError:
                    pass
            except (httpx.RequestError, httpx.HTTPStatusError,
                    OSError, ValueError) as e:
                logger.error("ExRate standalone failed: %s", e)
                try:
                    app.after(0, _done, False, f"Failed: {e}")
                except RuntimeError:
                    pass
            finally:
                loop.close()
        except (OSError, ValueError) as e:
            logger.error("ExRate file creation failed: %s", e)
            try:
                app.after(0, _done, False, f"Failed: {e}")
            except RuntimeError:
                pass

    threading.Thread(target=_worker, daemon=True, name="ExRateWorker").start()

