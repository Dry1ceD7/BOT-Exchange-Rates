#!/usr/bin/env python3
"""
core/paths.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Centralized Path Resolution
---------------------------------------------------------------------------
Provides a single get_project_root() that works correctly in both:
  - Source mode: python main.py
  - Frozen mode: PyInstaller .exe / .app

PyInstaller's __file__ resolves to the _MEI* temp extraction directory,
which breaks all relative data/ lookups. This module fixes that.

Frozen-mode writability (v3.2.8):
  The exe-dir data/ folder is the default in frozen mode (preserves existing
  installs). But Program Files is frequently read-only / UAC-virtualized, so
  if the exe dir is NOT writable we fall back to a per-user app-data dir:
    - Windows: %LOCALAPPDATA%/BOT_Exrate
    - else:    ~/.local/share/BOT_Exrate
  The dev-mode path is never changed.
"""

import os
import sys
from pathlib import Path

_APP_DIR_NAME = "BOT_Exrate"


def _is_writable(directory: str) -> bool:
    """Return True if we can create/write a file under ``directory``.

    Creates the directory if needed, then probes with a temp file. Any OSError
    (permission denied, read-only FS, UAC virtualization quirks) means we
    treat the location as not writable.
    """
    dir_path = Path(directory)
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = dir_path / ".write_probe"
    try:
        with probe.open("w", encoding="utf-8") as f:
            f.write("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _user_data_root() -> str:
    """Per-user writable app-data directory (used as a frozen-mode fallback)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return str(Path(base) / _APP_DIR_NAME)
    return str(Path.home() / ".local" / "share" / _APP_DIR_NAME)


def get_project_root() -> str:
    """
    Return the project root directory used as the base for data/.

    - Source (python main.py): the directory containing main.py. UNCHANGED.
    - Frozen (.exe / .app): the directory containing the executable IF it is
      writable; otherwise a per-user app-data dir (writable fallback) so the
      app never orphans its cache/backups/logs in a read-only Program Files.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.executable to the .exe path.
        # noqa: PTH120 — os.path.dirname preserves the exact exe-dir string
        # returned as the frozen project root (no symlink resolution).
        exe_dir = os.path.dirname(sys.executable)  # noqa: PTH120
        # Conservative: keep exe-dir data/ as default for existing installs.
        if _is_writable(str(Path(exe_dir) / "data")):
            return exe_dir
        # Read-only / UAC-virtualized install — fall back to per-user dir.
        return _user_data_root()
    # Source mode — main.py lives at project root (never changed).
    # noqa: PTH100,PTH120 — os.path.abspath does NOT resolve symlinks while
    # Path.resolve() does; this two-level-up string is the canonical project
    # root the whole app builds on, so keep the exact legacy computation.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # noqa: PTH100, PTH120


def harden_data_dirs(project_root: str) -> None:
    """Best-effort chmod 0o700 on the data/ root and its sensitive subdirs.

    Restricts cache, backups and logs to the owner only where the OS supports
    POSIX permissions. Silently ignores OSError (e.g. Windows / network FS).
    """
    root = Path(project_root)
    for target in (root / "data", root / "data" / "logs", root / "data" / "backups"):
        try:
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o700)
        except OSError:
            pass
