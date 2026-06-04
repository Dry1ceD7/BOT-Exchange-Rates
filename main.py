#!/usr/bin/env python3
"""
main.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Enterprise Desktop Edition
---------------------------------------------------------------------------
Entry point. Loads .env, prompts for API tokens on first use via
a registration dialog, ensures required directories exist, then
launches the GUI.

Headless CLI exit-code contract (see ``--help`` epilog):
    0  EXIT_OK        — all files succeeded (or scheduler stopped cleanly)
    1  EXIT_TOTAL     — total failure: every file failed
    2  EXIT_PARTIAL   — partial failure: some succeeded, some failed
    3  EXIT_CONFIG    — usage/config error: missing tokens, bad input path,
                        bad date, or no input directory
    4  EXIT_NOTHING   — nothing to do: no Excel files found to process
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

# ── Headless CLI exit-code contract (single source of truth) ─────────────
# Distinct codes so cron / monitoring wrappers can tell apart all-ok, total
# failure, partial failure, config errors, and an empty input folder. The
# full table is documented in the module docstring and the --help epilog.
EXIT_OK = 0
EXIT_TOTAL = 1
EXIT_PARTIAL = 2
EXIT_CONFIG = 3
EXIT_NOTHING = 4

_EXIT_CODE_EPILOG = """\
Headless exit codes (--headless):
  0  all files succeeded
  1  total failure (every file failed)
  2  partial failure (some succeeded, some failed)
  3  usage/config error (missing tokens, bad input path, bad date)
  4  nothing to do (no Excel files found)

Examples:
  python main.py --headless --input ./ledgers
  python main.py --headless --input ledger.xlsx --start-date 2025-01-02
  python main.py --headless --dry-run            # preview, no files modified
  python main.py --headless --quiet              # only the final summary
  python main.py --headless --json               # machine-readable summary
  python main.py --schedule 23:00 --input ./ledgers   # foreground scheduler
"""

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


def _show_tokens_required_message() -> None:
    """Explain that API keys are mandatory before the app exits.

    Shown when the first-run user closes the registration dialog without
    activating. Without this the window simply vanishes and looks like a
    crash. Best-effort native messagebox; falls back to stderr in headless /
    no-display environments so the reason is never lost.
    """
    message = (
        "API keys are required to use BOT Exchange Rate Processor.\n\n"
        "Get your tokens from the Bank of Thailand API portal "
        "(the portal link is in the registration dialog), then relaunch the "
        "app and enter them to activate."
    )
    shown = False
    with contextlib.suppress(Exception):
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("API Keys Required", message)
        root.destroy()
        shown = True
    if not shown:
        print(message, file=sys.stderr, flush=True)


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
        epilog=_EXIT_CODE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--dry-run", action="store_true",
        help="Preview changes without modifying files (headless/scheduler).",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Headless: suppress per-file lines; print only the final summary.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Headless: raise console log level to DEBUG for troubleshooting.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Headless: emit a machine-readable JSON summary to stdout.",
    )
    parser.add_argument(
        "--schedule", type=str, default=None, metavar="HH:MM",
        help="Run the auto-scheduler in the foreground (cron-friendly), firing "
             "a headless batch daily at HH:MM. Reuses core.scheduler.AutoScheduler.",
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
        sys.exit(_run_headless(args))

    # Foreground scheduler (headless server / cron-style box, no GUI session).
    # Reuses core.scheduler.AutoScheduler as-is; like --headless it must NOT be
    # blocked by the single-instance GUI guard, so it runs before that check.
    if args.schedule is not None:
        sys.exit(_run_schedule(args))

    # GUI launch only: if another GUI instance is already running, signal it
    # to restore from the tray and exit instead of opening a second window.
    from core.ipc import ping_running_instance
    if ping_running_instance():
        print("Another instance is already running. Signal sent to restore.")
        sys.exit(0)

    if not _tokens_present() and not _prompt_for_tokens():
        # The user closed the first-run registration dialog without entering
        # keys. Explain WHY the app is about to quit (it cannot work without
        # tokens) instead of vanishing silently like a crash, then exit with
        # the config exit code so a launcher can distinguish this from success.
        _show_tokens_required_message()
        sys.exit(EXIT_CONFIG)

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


def _set_console_log_level(level: int) -> None:
    """Adjust the root StreamHandler level (DEBUG for -v, WARNING for -q).

    Only the console handler is touched — the rotating file handler keeps its
    INFO level so app.log always retains the full per-run trail regardless of
    the requested console verbosity.
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(level)


def _collect_excel_files(input_path: str) -> list[str]:
    """Return the sorted full-path Excel files at ``input_path``.

    A single file yields a one-element list (or empty if it is not Excel); a
    directory is scanned non-recursively. Dotfiles are skipped. Keeps
    os.listdir + os.path.join so the returned strings match the exact prior
    full-path form fed to the engine.
    """
    excel_exts = (".xlsx", ".xlsm")
    if Path(input_path).is_file():
        return [input_path] if input_path.lower().endswith(excel_exts) else []
    return sorted([
        os.path.join(input_path, f)  # noqa: PTH118
        for f in os.listdir(input_path)  # noqa: PTH208
        if f.lower().endswith(excel_exts) and not f.startswith(".")
    ])


def _resolve_input_path(args: argparse.Namespace) -> str:
    """Resolve the headless/scheduler input path (default: data/input)."""
    if args.input is not None:
        # Keep as str: compared with .lower()/.endswith and joined as full paths.
        return args.input
    return str(Path(get_project_root()) / "data" / "input")


def _run_headless(args: argparse.Namespace) -> int:
    """Run the processor in headless (CLI) mode without GUI.

    Returns the process exit code (see the EXIT_* contract). Never calls
    sys.exit itself so it can be reused by the foreground scheduler.
    """
    if args.verbose:
        _set_console_log_level(logging.DEBUG)
    elif args.quiet:
        _set_console_log_level(logging.WARNING)

    quiet = args.quiet and not args.verbose

    def _emit(msg: str) -> None:
        """Print a per-file / progress line unless --quiet (JSON is silent)."""
        if not quiet and not args.json:
            print(msg)

    # Validate tokens — a config error, not a processing failure.
    if not _tokens_present():
        print(
            "ERROR: API tokens not configured. Run the GUI first to register "
            "tokens, or set BOT_TOKEN_EXG and BOT_TOKEN_HOL in .env",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    input_path = _resolve_input_path(args)
    if not Path(input_path).exists():
        print(f"ERROR: Input path not found: {input_path}", file=sys.stderr)
        return EXIT_CONFIG

    files = _collect_excel_files(input_path)
    if not files:
        # 'Nothing to do' is its own code so a cron user notices a misconfigured
        # folder instead of seeing a success-looking exit 0.
        print("No Excel files found to process.", file=sys.stderr)
        return EXIT_NOTHING

    _emit("BOT Exchange Rate Processor — Headless Mode")
    if args.dry_run:
        _emit("DRY RUN — no files will be modified")
    _emit(f"Found {len(files)} file(s) to process")

    # Determine start date
    if args.start_date:
        start_date = args.start_date
    else:
        from core.engine import LedgerEngine
        oldest, was_detected = LedgerEngine.prescan_oldest_date(files)
        start_date = oldest.strftime("%Y-%m-%d")
        flag = "auto-detected" if was_detected else "fallback"
        _emit(f"Start date: {start_date} ({flag})")

    success, fail, errors, audit_path = _process_headless_batch(
        files, start_date, dry_run=args.dry_run, quiet=quiet,
        json_mode=args.json,
    )

    if args.json:
        import json
        print(json.dumps({
            "succeeded": success,
            "failed": fail,
            "total": len(files),
            "dry_run": bool(args.dry_run),
            "audit_log": audit_path,
            "errors": errors,
        }))
    else:
        _emit("")
        print(f"Results: {success} succeeded, {fail} failed")
        if args.dry_run:
            print("DRY RUN — no files were modified")
        elif audit_path:
            print(f"Audit log: {audit_path}")
        if errors:
            print("Errors:", file=sys.stderr)
            for e in errors:
                print(f"  • {e}", file=sys.stderr)

    # Exit-code contract: distinguish total / partial / full success.
    if fail == 0:
        return EXIT_OK
    if success == 0:
        return EXIT_TOTAL
    return EXIT_PARTIAL


def _process_headless_batch(
    files: list[str],
    start_date: str,
    *,
    dry_run: bool = False,
    quiet: bool = False,
    json_mode: bool = False,
) -> tuple[int, int, list[str], str | None]:
    """Run one engine batch; return (success, fail, errors, audit_path).

    Owns its own AsyncClient so it is fully self-contained and reusable by both
    the one-shot headless path and the foreground scheduler's per-fire callback.
    """
    import asyncio

    import httpx

    async def _run() -> tuple[int, int, list[str], str | None]:
        from core.api_client import BOTClient
        from core.engine import LedgerEngine

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            api = BOTClient(client)
            engine = LedgerEngine(api)

            def progress_cb(
                idx: int, total: int, fname: str, error: str | None
            ) -> None:
                if quiet or json_mode:
                    return
                prefix = "[SIM] " if dry_run else ""
                if error:
                    print(f"  {prefix}[{idx}/{total}] {fname} — SKIPPED: {error}")
                else:
                    print(f"  {prefix}[{idx}/{total}] {fname} — OK")

            # Let the engine own the audit log exactly like the GUI handler
            # does (gui/handlers.py): when no AuditLogger is injected on a real
            # run, process_batch creates, populates per-cell records, summarizes,
            # finalizes, and prunes its OWN CSV. Surfacing engine.last_audit_path
            # points the user at that single populated file — passing our own
            # logger here would orphan the real log and advertise a hollow one.
            # Dry runs intentionally write no audit log (no files modified).
            success, fail, errors = await engine.process_batch(
                files, start_date=start_date, progress_cb=progress_cb,
                dry_run=dry_run,
            )
            return success, fail, errors, engine.last_audit_path

    return asyncio.run(_run())


def _run_schedule(args: argparse.Namespace) -> int:
    """Run core.scheduler.AutoScheduler in the foreground (cron-friendly).

    The scheduler polls on its own daemon Timer thread and fires a headless
    batch at args.schedule (HH:MM) each day; this function keeps the main
    thread alive and blocks until interrupted (Ctrl-C / SIGTERM). Reuses the
    AutoScheduler unchanged — we only supply the callback and own the wait loop.

    Returns EXIT_CONFIG for an invalid time / missing tokens, else EXIT_OK on a
    clean shutdown.
    """
    import re
    import time

    time_str = args.schedule.strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str):
        print(
            f"ERROR: --schedule expects HH:MM (24h), got: {args.schedule!r}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    if not _tokens_present():
        print(
            "ERROR: API tokens not configured. Set BOT_TOKEN_EXG and "
            "BOT_TOKEN_HOL (or register via the GUI) before scheduling.",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    if args.verbose:
        _set_console_log_level(logging.DEBUG)
    elif args.quiet:
        _set_console_log_level(logging.WARNING)

    watch_path = _resolve_input_path(args)

    from core.scheduler import AutoScheduler

    def _on_fire(files: list[str]) -> None:
        # Runs on the scheduler's Timer thread. Resolve a start date the same
        # way the one-shot headless path does, then process the batch in place.
        from core.engine import LedgerEngine
        if args.start_date:
            start_date = args.start_date
        else:
            oldest, _ = LedgerEngine.prescan_oldest_date(files)
            start_date = oldest.strftime("%Y-%m-%d")
        success, fail, _errors, _audit = _process_headless_batch(
            files, start_date, dry_run=args.dry_run,
            quiet=args.quiet and not args.verbose, json_mode=False,
        )
        logging.getLogger("main.schedule").info(
            "Scheduled run complete: %d succeeded, %d failed%s",
            success, fail, " (dry-run)" if args.dry_run else "",
        )

    scheduler = AutoScheduler()
    scheduler.start(
        time_str=time_str, watch_paths=[watch_path], callback=_on_fire,
    )
    mode = " (dry-run)" if args.dry_run else ""
    print(
        f"Scheduler running in foreground{mode}: daily at {time_str}, "
        f"watching {watch_path}. Press Ctrl-C to stop."
    )

    try:
        # Block the main thread; the scheduler's daemon timer does the work.
        while scheduler.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping scheduler…")
    finally:
        scheduler.stop()
    return EXIT_OK


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
