#!/usr/bin/env python3
"""
gui/os_open.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Shared OS file-manager launcher.
---------------------------------------------------------------------------
Single owner of the platform-safe "open this folder" logic, previously
duplicated between gui/panels/settings_modal.py and gui/app.py (wave-4
consolidation follow-up). The stricter settings_modal variant won:
resolve()-d, is_dir()-checked, bool-returning, fixed argv, never raises.
"""

import logging
import platform
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def open_folder(folder: str) -> bool:
    """Open ``folder`` in the OS file manager.

    Mirrors app.py:_reveal_file's platform-safe launch logic but targets a
    directory (no file selection). The path is resolve()-d and checked
    to be a directory before handing a fixed argv to the OS launcher, so the
    subprocess call never receives untrusted/shell-interpolated input.

    Returns True on a successful launch, False otherwise (missing dir or
    OSError). Never raises — callers surface failure to the user.
    """
    # SEC: resolve symlinks for the security check, then verify it's a dir.
    resolved = Path(folder).resolve()
    if not resolved.is_dir():
        logger.warning("Open-folder target is not a directory: %s", folder)
        return False
    target = str(resolved)
    try:
        system = platform.system()
        # noqa S603/S607: target is resolve()-d and is_dir()-checked above;
        # each call uses the OS-standard file-manager launcher with fixed argv.
        if system == "Darwin":
            subprocess.Popen(["open", target])  # noqa: S603, S607
        elif system == "Windows":
            subprocess.Popen(["explorer", target])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", target])  # noqa: S603, S607
        return True
    except OSError as e:
        logger.debug("File manager open failed: %s", e)
        return False
