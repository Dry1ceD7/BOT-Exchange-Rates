#!/usr/bin/env python3
"""
main.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Enterprise Desktop Edition
---------------------------------------------------------------------------
Entry point. Loads .env, prompts for API tokens on first use via
a registration dialog, ensures required directories exist, then
launches the GUI.
"""

import logging
import logging.handlers
import os
import sys

# Explicitly insert current directory to Python Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import messagebox

from dotenv import load_dotenv

from core.paths import get_project_root

# Securely load API Keys to os.environ BEFORE anything else
ENV_PATH = os.path.join(get_project_root(), ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ── Sentry Telemetry (v3.2.4) ───────────────────────────────────────────
# Conditionally initialize Sentry crash reporting. If SENTRY_DSN is not
# set, Sentry is completely disabled — zero overhead, zero network calls.
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk

        from core.version import __version__
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            release=f"bot-exrate@{__version__}",
            traces_sample_rate=0.2,  # 20% of transactions for performance monitoring
            send_default_pii=False,  # Never send user PII
        )
    except Exception:
        pass  # Sentry is optional — never block app startup

# ── Configure root logger — routes ALL log output to file + console ──────
_LOG_DIR = os.path.join(get_project_root(), "data")
os.makedirs(_LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(_LOG_DIR, "app.log"),
            maxBytes=5_000_000,   # 5 MB per file
            backupCount=3,        # keep app.log.1, .2, .3
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)


# ── Cold-Start: Ensure required directories exist ────────────────────────
def _ensure_directories():
    """
    Git does not track empty folders. On a fresh clone, data/input/
    and data/backups/ will not exist. Create them proactively.
    (data/ and data/backups/ are also created by database.py and
    backup_manager.py singletons, but data/input/ is NOT.)
    """
    project_root = get_project_root()
    for subdir in ["data", "data/input", "data/backups", "data/logs"]:
        os.makedirs(os.path.join(project_root, subdir), exist_ok=True)


# ── Token Check + Registration Dialog ───────────────────────────────────
def _tokens_present() -> bool:
    """Return True if both BOT API tokens are set in the environment."""
    return bool(os.environ.get("BOT_TOKEN_EXG")) and bool(
        os.environ.get("BOT_TOKEN_HOL")
    )


def _prompt_for_tokens() -> bool:
    """
    Launch the registration dialog to collect API tokens.
    Returns True if the user activated successfully, False otherwise.
    """
    import customtkinter as ctk

    from gui.panels.token_dialog import TokenRegistrationDialog

    root = ctk.CTk()
    root.withdraw()

    dialog = TokenRegistrationDialog(root, env_path=ENV_PATH)
    root.wait_window(dialog)

    activated = dialog.activated
    root.destroy()
    return activated


def main():
    """Ensures directories, validates/prompts tokens, then starts the app."""
    _ensure_directories()

    from core.ipc import ping_running_instance
    if ping_running_instance():
        print("Another instance is already running. Signal sent to restore.")
        sys.exit(0)

    # ── v3.1.0: CLI argument parsing ─────────────────────────────────
    import argparse
    parser = argparse.ArgumentParser(
        description="BOT Exchange Rate Processor — Enterprise Desktop Edition",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run in headless mode (no GUI). Process files and exit.",
    )
    parser.add_argument(
        "--input", "-i", type=str, default=None,
        help="Path to an Excel file or directory of Excel files to process.",
    )
    parser.add_argument(
        "--start-date", "-s", type=str, default=None,
        help="Start date for rate extraction (YYYY-MM-DD). Defaults to auto-detect.",
    )
    args = parser.parse_args()

    if args.headless:
        _run_headless(args)
        return

    if not _tokens_present():
        if not _prompt_for_tokens():
            sys.exit(0)

    from core.ipc import SingleInstanceServer
    from gui.app import BOTExrateApp
    app = BOTExrateApp()

    # Start IPC listener to restore application if another tries to launch
    ipc_server = SingleInstanceServer(
        on_restore=lambda: app.after(0, app.restore_from_tray)
    )
    ipc_server.start()

    try:
        app.mainloop()
    finally:
        ipc_server.stop()


def _run_headless(args):
    """Run the processor in headless (CLI) mode without GUI."""
    import asyncio

    import httpx

    # Validate tokens
    if not _tokens_present():
        print("ERROR: API tokens not configured.")
        print("Run the GUI first to register tokens, or set BOT_TOKEN_EXG and BOT_TOKEN_HOL in .env")
        sys.exit(1)

    # Collect files
    input_path = args.input
    if input_path is None:
        input_path = os.path.join(get_project_root(), "data", "input")

    if not os.path.exists(input_path):
        print(f"ERROR: Input path not found: {input_path}")
        sys.exit(1)

    excel_exts = (".xlsx", ".xlsm")
    if os.path.isfile(input_path):
        files = [input_path] if input_path.lower().endswith(excel_exts) else []
    else:
        files = sorted([
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.lower().endswith(excel_exts) and not f.startswith(".")
        ])

    if not files:
        print("No Excel files found to process.")
        sys.exit(0)

    print("BOT Exchange Rate Processor — Headless Mode")
    print(f"Found {len(files)} file(s) to process")

    # Determine start date
    if args.start_date:
        start_date = args.start_date
    else:
        from core.engine import LedgerEngine
        oldest, was_detected = LedgerEngine.prescan_oldest_date(files)
        start_date = oldest.strftime("%Y-%m-%d")
        flag = "auto-detected" if was_detected else "fallback"
        print(f"Start date: {start_date} ({flag})")

    # Run async batch
    async def _run():
        from core.api_client import BOTClient
        from core.audit_logger import AuditLogger
        from core.engine import LedgerEngine

        audit = AuditLogger()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api)

            def progress_cb(idx, total, fname, error):
                if error:
                    print(f"  [{idx}/{total}] {fname} — SKIPPED: {error}")
                else:
                    print(f"  [{idx}/{total}] {fname} — OK")

            success, fail, errors = await engine.process_batch(
                files, start_date=start_date, progress_cb=progress_cb,
            )

        audit.log_batch_summary(
            total_files=len(files),
            success=success,
            failed=fail,
            anomalies_detected=0,
        )
        audit_path = audit.finalize()

        print(f"\nResults: {success} succeeded, {fail} failed")
        print(f"Audit log: {audit_path}")
        if errors:
            print("Errors:")
            for e in errors:
                print(f"  • {e}")
        return fail

    fail_count = asyncio.run(_run())
    sys.exit(1 if fail_count > 0 else 0)


import traceback  # noqa: E402


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """
    Fallback handler to catch fatal errors when running without a console.
    Crucial for Windows --noconsole mode so crash logs are not lost.

    v3.2.4: Always emits to stderr first (guaranteed), then attempts
    Sentry upload, file logging, and GUI popup as best-effort layers.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

    # Layer 0: Always emit to stderr — this is the only guaranteed output
    try:
        print(f"\n[FATAL] {error_msg}", file=sys.stderr, flush=True)
    except Exception:
        pass

    # Layer 1: Forward to Sentry if initialized
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc_value)
        sentry_sdk.flush(timeout=2.0)
    except Exception:
        pass

    # Layer 2: Write to local error.log
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- FATAL ERROR ---\n{error_msg}\n")
    except Exception:
        pass

    # Layer 3: Show GUI popup (best-effort, may fail in headless/noconsole)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Fatal Application Error",
            f"A critical crash occurred:\n\n{exc_value}\n\nPlease check error.log for full details."
        )
        root.destroy()
    except Exception:
        pass

sys.excepthook = global_exception_handler

if __name__ == "__main__":
    main()

