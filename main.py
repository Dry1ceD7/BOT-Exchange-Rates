#!/usr/bin/env python3
"""
main.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.0) - Enterprise Desktop Edition
---------------------------------------------------------------------------
Entry point. Loads .env, validates API tokens BEFORE GUI init,
ensures required directories exist, and exits with a clear error
popup if tokens are missing.
"""

import os
import sys

# Explicitly insert current directory to Python Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import messagebox

from dotenv import load_dotenv

# Securely load API Keys to os.environ BEFORE anything else
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_path)


# ── Cold-Start: Ensure required directories exist ────────────────────────
def _ensure_directories():
    """
    Git does not track empty folders. On a fresh clone, data/input/
    and data/backups/ will not exist. Create them proactively.
    (data/ and data/backups/ are also created by database.py and
    backup_manager.py singletons, but data/input/ is NOT.)
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    for subdir in ["data", "data/input", "data/backups"]:
        os.makedirs(os.path.join(project_root, subdir), exist_ok=True)


# ── Early Token Validation ───────────────────────────────────────────────
def _validate_tokens():
    """
    Checks for required BOT API tokens BEFORE the GUI loads.
    Shows a native error dialog and exits if missing.
    """
    missing = []
    if not os.environ.get("BOT_TOKEN_EXG"):
        missing.append("BOT_TOKEN_EXG")
    if not os.environ.get("BOT_TOKEN_HOL"):
        missing.append("BOT_TOKEN_HOL")

    if missing:
        root = tk.Tk()
        root.withdraw()  # Hide the empty root window
        messagebox.showerror(
            "CRITICAL: API Tokens Missing",
            f"The following required tokens are not set in your .env file:\n\n"
            f"  • {chr(10).join(missing)}\n\n"
            f"Please copy .env.example to .env and add your credentials.\n\n"
            f"Register at: https://apiportal.bot.or.th/"
        )
        root.destroy()
        sys.exit(1)


def main():
    """Ensures directories, validates tokens, then starts the application."""
    _ensure_directories()
    _validate_tokens()

    from gui.app import BOTExrateApp
    app = BOTExrateApp()
    app.mainloop()


import traceback  # noqa: E402


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """
    Fallback handler to catch fatal errors when running without a console.
    Crucial for Windows --noconsole mode so crash logs are not lost.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

    # Write to local error.log
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- FATAL ERROR ---\n{error_msg}\n")
    except Exception:
        pass

    # Show GUI popup
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
