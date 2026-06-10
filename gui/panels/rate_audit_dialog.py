#!/usr/bin/env python3
"""
gui/panels/rate_audit_dialog.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Rate Audit (verify old data against BOT)
---------------------------------------------------------------------------
Lets the operator pick an existing workbook, re-verify the hard USD/EUR rate
values in its "ExRate" master sheet against the live Bank of Thailand values,
and auto-correct any differing trading-day cell (the file is backed up first).
A report dialog then lists every change — date, cell, currency, old → new, and
why — with a Revert button that restores the pre-correction backup.

Weekend/holiday rows are never touched (they are blank by design); the scan +
correction logic lives in core/rate_audit.py. This module is only the UI + the
background worker that drives it without blocking Tk.
"""
import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core.i18n import tr
from core.rate_audit import LAYOUT_ERROR_MSG, write_audit_csv
from gui.theme import get_theme

logger = logging.getLogger(__name__)


def show_rate_audit_dialog(app) -> None:
    """Entry point: pick a workbook, then run the audit in the background.

    Called by app._open_rate_audit, which has already set ``_exrate_running``
    and disabled the sibling Process/Revert buttons. If the user cancels the
    file picker, release that lock immediately so the UI is not left stuck.
    """
    filepath = filedialog.askopenfilename(
        title=tr("rateaudit.picker_title"),
        # .xlsm stays selectable: the workbook pipeline loads macro-enabled
        # files with keep_vba so their VBA project survives the round-trip.
        filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")],
    )
    if not filepath:
        app._exrate_running = False  # nothing spawned — release the guard
        return
    _launch_worker(app, filepath)


def _set_status(app, text: str, color: str | None = None) -> None:
    t = get_theme()
    if hasattr(app, "lbl_status"):
        with contextlib.suppress(Exception):
            app.lbl_status.configure(
                text=text, text_color=color or t["text_secondary"]
            )


def _launch_worker(app, filepath: str) -> None:
    """Run StandaloneRateAuditor on ``filepath`` in a daemon worker thread."""
    t = get_theme()
    event_bus = getattr(app, "event_bus", None)

    def _status_cb(msg: str) -> None:
        # _safe_marshal no-ops once the app is closing and swallows both
        # RuntimeError AND TclError (TclError is NOT a RuntimeError subclass),
        # so a teardown race can never kill the worker thread.
        # ``msg`` is the English progress detail from core/rate_audit.py
        # (core strings stay English per the i18n SCOPE note).
        app._safe_marshal(
            _set_status, app, tr("rateaudit.status_progress", msg=msg)
        )

    def _done_ok(report, csv_path) -> None:
        app._exrate_running = False
        n = report.change_count
        _set_status(
            app,
            tr("rateaudit.status_applied", count=n)
            if n else tr("rateaudit.status_all_match"),
            t["process_text"] if n else t["text_secondary"],
        )
        with contextlib.suppress(Exception):
            _show_report_dialog(app, report, csv_path)

    def _done_err(msg: str) -> None:
        app._exrate_running = False
        # The layout refusal is the one structured, user-meaningful failure —
        # show its translated twin; other details stay verbatim (English).
        if msg == LAYOUT_ERROR_MSG:
            msg = tr("rateaudit.err_layout")
        _set_status(app, tr("rateaudit.status_failed", msg=msg), t["warning"])

    def _worker() -> None:
        import httpx

        from core.api_client import CLIENT_TIMEOUT, BOTClient
        from core.engine import LedgerEngine
        from core.rate_audit import StandaloneRateAuditor

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _run():
                async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                    api = BOTClient(client)
                    engine = LedgerEngine(api, event_bus=event_bus)
                    return await StandaloneRateAuditor(engine).run(
                        filepath, apply=True, status_cb=_status_cb,
                    )

            report = loop.run_until_complete(_run())
            csv_path = None
            with contextlib.suppress(Exception):
                csv_path = write_audit_csv(report)
            app._safe_marshal(_done_ok, report, csv_path)
        except Exception as e:  # noqa: BLE001 — surfaced to the user via _done_err
            logger.exception("Rate audit worker failed")
            app._safe_marshal(_done_err, str(e))
        finally:
            with contextlib.suppress(Exception):
                loop.close()
            if hasattr(app, "thread_registry"):
                with contextlib.suppress(Exception):
                    app.thread_registry.unregister("RateAuditWorker")

    worker = threading.Thread(
        target=_worker, daemon=True, name="RateAuditWorker"
    )
    if hasattr(app, "_register_worker"):
        with contextlib.suppress(Exception):
            app._register_worker(worker, "RateAuditWorker")
    _set_status(app, tr("rateaudit.status_starting"), t["text_secondary"])
    worker.start()


def _show_report_dialog(app, report, csv_path: str | None) -> None:
    """Modal report listing every correction (date, cell, old → new, why)."""
    t = get_theme()
    dlg = ctk.CTkToplevel(app)
    dlg.title(tr("rateaudit.report_title"))
    dlg.geometry("720x480")
    with contextlib.suppress(Exception):
        dlg.transient(app)
    with contextlib.suppress(Exception):
        dlg.grab_set()
    with contextlib.suppress(Exception):
        dlg.configure(fg_color=t["modal_bg"])

    fname = (
        Path(report.file).name if report.file
        else tr("rateaudit.workbook_fallback")
    )
    n = report.change_count
    if report.applied and n:
        head = tr("rateaudit.head_corrected", count=n, fname=fname)
    elif n:
        head = tr("rateaudit.head_differences", count=n, fname=fname)
    else:
        head = tr("rateaudit.head_all_match", fname=fname)
    ctk.CTkLabel(
        dlg, text=head, font=ctk.CTkFont(size=16, weight="bold"),
        text_color=t["modal_text"], wraplength=680,
    ).pack(pady=(16, 4), padx=16)

    sub = tr(
        "rateaudit.sub_scanned",
        rows=report.scanned_rows, cells=report.compared_cells,
    )
    if report.unverifiable:
        sub += "  " + tr(
            "rateaudit.sub_unverifiable", count=report.unverifiable
        )
    ctk.CTkLabel(
        dlg, text=sub, font=ctk.CTkFont(size=11), text_color=t["modal_muted"],
        wraplength=680,
    ).pack(padx=16)

    body = ctk.CTkScrollableFrame(dlg, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=12)

    if report.changes:
        hdr_font = ctk.CTkFont(size=11, weight="bold")
        cell_font = ctk.CTkFont(size=11)
        headers = (
            tr("rateaudit.col_date"), tr("rateaudit.col_cell"),
            tr("rateaudit.col_currency_type"), tr("rateaudit.col_old"),
            tr("rateaudit.col_new"), tr("rateaudit.col_why"),
        )
        for col, label in enumerate(headers):
            ctk.CTkLabel(
                body, text=label, font=hdr_font, text_color=t["modal_muted"],
                anchor="w",
            ).grid(row=0, column=col, sticky="w", padx=6, pady=(0, 6))
        for r, ch in enumerate(report.changes, start=1):
            values = (
                ch.rate_date.strftime("%Y-%m-%d"),
                ch.cell,
                f"{ch.currency} {ch.rate_type}",
                tr("rateaudit.blank_value")
                if ch.old_value is None else str(ch.old_value),
                str(ch.new_value),
                ch.reason,
            )
            for col, val in enumerate(values):
                ctk.CTkLabel(
                    body, text=val, font=cell_font, text_color=t["modal_text"],
                    anchor="w", justify="left",
                    wraplength=240 if col == 5 else 0,
                ).grid(row=r, column=col, sticky="w", padx=6, pady=2)
    else:
        ctk.CTkLabel(
            body,
            text=tr("rateaudit.no_corrections"),
            font=ctk.CTkFont(size=12), text_color=t["modal_text"],
            wraplength=640,
        ).pack(pady=24)

    btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
    btn_row.pack(fill="x", padx=16, pady=(0, 14))

    if report.applied and report.backup_path and report.changes:
        def _revert() -> None:
            # Restore the EXACT pre-correction backup we captured, not merely
            # the latest — a batch could have snapshotted the file since.
            # Route through the guarded app entry: our _exrate_running lease
            # was released before this dialog opened, so the entry re-checks
            # the batch/revert/ExRate busy flags and raises _revert_running,
            # ensuring the RevertWorker can never run concurrently with a
            # scheduler-fired batch on the same workbook.
            try:
                started = app._start_guarded_revert(
                    report.file, report.backup_path,
                )
            except Exception:  # noqa: BLE001 — keep the dialog usable
                logger.exception("Rate audit revert launch failed")
                started = False
            if not started:
                # Refused (another operation owns the file) or failed to
                # launch — say so here; the grab hides the main status bar.
                with contextlib.suppress(Exception):
                    lbl_revert_status.configure(
                        text=tr("rateaudit.revert_busy"),
                    )
                return
            with contextlib.suppress(Exception):
                dlg.destroy()

        ctk.CTkButton(
            btn_row, text=tr("rateaudit.btn_revert"),
            fg_color=t["warning"],
            hover_color=t.get("warning_hover", t["warning"]),
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_revert,
        ).pack(side="left")
        lbl_revert_status = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=11),
            text_color=t["warning"], wraplength=320, justify="left",
        )
        lbl_revert_status.pack(side="left", padx=10)

    if csv_path:
        ctk.CTkLabel(
            btn_row, text=tr("rateaudit.csv_label", name=Path(csv_path).name),
            font=ctk.CTkFont(size=10), text_color=t["modal_muted"],
        ).pack(side="left", padx=12)

    ctk.CTkButton(
        btn_row, text=tr("rateaudit.btn_close"), command=dlg.destroy,
    ).pack(side="right")
