#!/usr/bin/env python3
"""
main.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.8) - Enterprise Desktop Edition
---------------------------------------------------------------------------
Entry point. Loads .env, prompts for API tokens on first use via
a registration dialog, ensures required directories exist, then
launches the GUI.
"""

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


# ── Cold-Start: Ensure required directories exist ────────────────────────
def _ensure_directories():
    """
    Git does not track empty folders. On a fresh clone, data/input/
    and data/backups/ will not exist. Create them proactively.
    (data/ and data/backups/ are also created by database.py and
    backup_manager.py singletons, but data/input/ is NOT.)
    """
    project_root = get_project_root()
    for subdir in ["data", "data/input", "data/backups"]:
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

    if not _tokens_present():
        if not _prompt_for_tokens():
            sys.exit(0)

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
