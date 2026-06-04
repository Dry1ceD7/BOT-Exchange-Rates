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

import argparse
import logging
import logging.handlers
import os
import sys
import traceback
import types
from pathlib import Path

# Explicitly insert current directory to Python Path.
# noqa: PTH100,PTH120 — keep os.path's exact (no-symlink) string so the
# sys.path entry matches the legacy bootstrap behavior on the frozen target.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: PTH100, PTH120

import contextlib
import tkinter as tk
from tkinter import messagebox

from dotenv import load_dotenv

from core.paths import get_project_root, harden_data_dirs

# Securely load API Keys to os.environ BEFORE anything else.
# Keep ENV_PATH as str: passed to load_dotenv and TokenRegistrationDialog.
ENV_PATH = str(Path(get_project_root()) / ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ── Sentry Telemetry (v3.2.4) ───────────────────────────────────────────
# Conditionally initialize Sentry crash reporting. If SENTRY_DSN is not
# set, Sentry is completely disabled — zero overhead, zero network calls.
def _scrubber_token_values() -> list:
    """Collect live token values for the Sentry scrubber.

    Sources tokens via core.secure_tokens.get_token (keychain + .env) so the
    scrubber still works after the env→keychain migration empties os.environ.
    Exception-guarded and cached per event batch (see _sentry_token_scrubber).
    """
    tokens: list = []
    try:
        from core.secure_tokens import get_token
        for env_key in ("BOT_TOKEN_EXG", "BOT_TOKEN_HOL"):
            with contextlib.suppress(Exception):
                val = get_token(env_key)
                if val:
                    tokens.append(val)
    except Exception:
        # Fallback to os.environ if secure_tokens is unavailable.
        for env_key in ("BOT_TOKEN_EXG", "BOT_TOKEN_HOL"):
            val = os.environ.get(env_key)
            if val:
                tokens.append(val)
    return tokens


def _sentry_token_scrubber(event, hint):
    """Sentry before_send hook: replace known token values with '***'.

    Tokens can otherwise surface in event messages, exception values, or
    request data. We recursively walk the event and substitute any known
    token string. Returns the (mutated) event so it is still sent.

    Token values are resolved fresh for each event batch (keychain + .env)
    and cached on the function for the duration of this call.
    """
    tokens = _scrubber_token_values()
    if not tokens:
        return event

    def _scrub(obj):
        if isinstance(obj, str):
            out = obj
            for tok in tokens:
                out = out.replace(tok, "***")
            return out
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_scrub(v) for v in obj]
        return obj

    try:
        return _scrub(event)
    except Exception:
        return event


_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    # Sentry is optional — never block app startup
    with contextlib.suppress(Exception):
        import sentry_sdk

        from core.version import __version__
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            release=f"bot-exrate@{__version__}",
            traces_sample_rate=0.2,  # 20% of transactions for performance monitoring
            send_default_pii=False,  # Never send user PII
            before_send=_sentry_token_scrubber,  # Redact tokens from events
        )

# ── Configure root logger — routes ALL log output to file + console ──────
_LOG_DIR = Path(get_project_root()) / "data"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "app.log",
            maxBytes=5_000_000,   # 5 MB per file
            backupCount=3,        # keep app.log.1, .2, .3
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)

# SECURITY: redact BOT API token values from all log records before they
# reach the file/console handlers (defends app.log + Sentry breadcrumbs).
with contextlib.suppress(Exception):
    from core.api_client import install_token_redaction_filter
    install_token_redaction_filter()


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
        (Path(project_root) / subdir).mkdir(parents=True, exist_ok=True)
    # Restrict data dir perms (0700) so cached rates, backups, and audit
    # logs are not world-readable on shared/server-share installs.
    harden_data_dirs(project_root)


# ── Token Check + Registration Dialog ───────────────────────────────────
def _tokens_present() -> bool:
    """Return True if both BOT API tokens are available (keychain or env)."""
    from core.secure_tokens import get_token
    return bool(get_token("BOT_TOKEN_EXG")) and bool(
        get_token("BOT_TOKEN_HOL")
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


def _purge_credentials() -> None:
    """Delete both BOT API tokens from the OS keychain and report the result.

    Invoked by the Windows uninstaller via --purge-credentials so secrets do
    not survive an application removal.
    """
    from core.secure_tokens import delete_token
    removed = 0
    for env_key in ("BOT_TOKEN_EXG", "BOT_TOKEN_HOL"):
        if delete_token(env_key):
            removed += 1
    print(f"Purged {removed} stored credential(s) from the OS keychain.")


def main():
    """Ensures directories, validates/prompts tokens, then starts the app."""
    _ensure_directories()

    # ── v3.1.0: CLI argument parsing ─────────────────────────────────
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
    parser.add_argument(
        "--purge-credentials", action="store_true",
        help="Delete stored BOT API tokens from the OS keychain and exit. "
             "Invoked by the Windows uninstaller.",
    )
    args = parser.parse_args()

    # Purge stored credentials early — before the single-instance check and
    # any token validation — so the uninstaller can wipe secrets even when
    # the app is running or no tokens are currently configured.
    if args.purge_credentials:
        _purge_credentials()
        sys.exit(0)

    # Headless batch runs must NOT be blocked by a running GUI. They are
    # stateless and coordinate file safety via per-file backups + the batch
    # lock — not the IPC restore channel. Running the single-instance guard
    # here would make scheduled (cron / Task Scheduler) runs ping the open
    # GUI, print the restore message, and exit 0 without processing anything,
    # silently breaking the advertised unattended workflow.
    if args.headless:
        _run_headless(args)
        return

    # GUI launch only: if another GUI instance is already running, signal it
    # to restore from the tray and exit instead of opening a second window.
    from core.ipc import ping_running_instance
    if ping_running_instance():
        print("Another instance is already running. Signal sent to restore.")
        sys.exit(0)

    if not _tokens_present() and not _prompt_for_tokens():
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


def _run_headless(args: argparse.Namespace) -> None:
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
        # Keep input_path as str: compared with .lower()/.endswith below and
        # used to build the sorted full-path processing list.
        input_path = str(Path(get_project_root()) / "data" / "input")

    if not Path(input_path).exists():
        print(f"ERROR: Input path not found: {input_path}")
        sys.exit(1)

    excel_exts = (".xlsx", ".xlsm")
    if Path(input_path).is_file():
        files = [input_path] if input_path.lower().endswith(excel_exts) else []
    else:
        # Keep os.listdir + os.path.join: `files` are full-path strings fed to
        # the engine, and sorting the joined paths is the exact prior behavior.
        files = sorted([
            os.path.join(input_path, f)  # noqa: PTH118
            for f in os.listdir(input_path)  # noqa: PTH208
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
        from core.engine import LedgerEngine

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api)

            def progress_cb(idx: int, total: int, fname: str, error: str | None) -> None:
                if error:
                    print(f"  [{idx}/{total}] {fname} — SKIPPED: {error}")
                else:
                    print(f"  [{idx}/{total}] {fname} — OK")

            # Let the engine own the audit log exactly like the GUI handler
            # does (gui/handlers.py): when no AuditLogger is injected on a real
            # run, process_batch creates, populates per-cell records, summarizes,
            # finalizes, and prunes its OWN CSV. Surfacing engine.last_audit_path
            # below points the user at that single populated file — passing our
            # own logger here would orphan the real log and advertise a hollow one.
            success, fail, errors = await engine.process_batch(
                files, start_date=start_date, progress_cb=progress_cb,
            )

        print(f"\nResults: {success} succeeded, {fail} failed")
        if engine.last_audit_path:
            print(f"Audit log: {engine.last_audit_path}")
        if errors:
            print("Errors:")
            for e in errors:
                print(f"  • {e}")
        return fail

    fail_count = asyncio.run(_run())
    sys.exit(1 if fail_count > 0 else 0)


def global_exception_handler(
    exc_type: type,
    exc_value: BaseException,
    exc_traceback: types.TracebackType | None,
) -> None:
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
    with contextlib.suppress(Exception):
        print(f"\n[FATAL] {error_msg}", file=sys.stderr, flush=True)

    # Layer 1: Forward to Sentry if initialized
    with contextlib.suppress(Exception):
        import sentry_sdk
        sentry_sdk.capture_exception(exc_value)
        sentry_sdk.flush(timeout=2.0)

    # Layer 2: Write to local error.log
    log_dir = Path(get_project_root()) / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "error.log"
    with contextlib.suppress(Exception), log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n--- FATAL ERROR ---\n{error_msg}\n")

    # Layer 3: Show GUI popup (best-effort, may fail in headless/noconsole)
    with contextlib.suppress(Exception):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Fatal Application Error",
            f"A critical crash occurred:\n\n{exc_value}\n\nPlease check error.log for full details."
        )
        root.destroy()

sys.excepthook = global_exception_handler

if __name__ == "__main__":
    main()
