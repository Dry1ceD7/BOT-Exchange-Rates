#!/usr/bin/env python3
"""
main.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.3.1) - Fail-Safe Enterprise
---------------------------------------------------------------------------
Entry point. Loads .env, validates API tokens BEFORE GUI init,
and exits with a clear error popup if tokens are missing.
"""

import sys
import os

# Explicitly insert current directory to Python Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Securely load API Keys to os.environ BEFORE anything else
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_path)

# ── FIX 1: Early Token Validation ────────────────────────────────────────
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
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()  # Hide the empty root window
        messagebox.showerror(
            "CRITICAL: API Tokens Missing",
            f"The following required tokens are not set in your .env file:\n\n"
            f"  • {chr(10).join(missing)}\n\n"
            f"Please add them to:\n{env_path}\n\n"
            f"The application cannot start without valid BOT API credentials."
        )
        root.destroy()
        sys.exit(1)


def main():
    """Validates tokens, then starts the CustomTkinter application."""
    _validate_tokens()

    from gui.app import BOTExrateApp
    app = BOTExrateApp()
    app.mainloop()


if __name__ == "__main__":
    main()
