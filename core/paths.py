#!/usr/bin/env python3
"""
core/paths.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.4) — Centralized Path Resolution
---------------------------------------------------------------------------
Provides a single get_project_root() that works correctly in both:
  - Source mode: python main.py
  - Frozen mode: PyInstaller .exe / .app

PyInstaller's __file__ resolves to the _MEI* temp extraction directory,
which breaks all relative data/ lookups. This module fixes that.
"""

import os
import sys


def get_project_root() -> str:
    """
    Return the project root directory.

    - Frozen (.exe / .app): the directory containing the executable.
    - Source (python main.py): the directory containing main.py.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.executable to the .exe path
        return os.path.dirname(sys.executable)
    # Source mode — main.py lives at project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
