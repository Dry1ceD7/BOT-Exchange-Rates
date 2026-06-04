#!/usr/bin/env python3
"""
core/workbook_io.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Workbook I/O guardrails
---------------------------------------------------------------------------
Small, shared helpers for the in-place openpyxl save pipeline.

ensure_disk_space is the single source of truth for the pre-save free-space
guard used by both the ledger write pipeline and the standalone updater. It is
deliberately implemented with a module-level ``import shutil`` + a late
``shutil.disk_usage(...)`` lookup so that tests which patch the shared shutil
module object (e.g. ``monkeypatch.setattr(engine_mod.shutil, 'disk_usage', ...)``)
see the patch — module objects are singletons.
"""

import shutil
from pathlib import Path


def ensure_disk_space(target_dir: Path, min_mb: int) -> None:
    """Raise OSError if free disk space at target_dir is below min_mb.

    Args:
        target_dir: Directory the workbook will be saved into.
        min_mb: Minimum required free space in megabytes.

    Raises:
        OSError: If free space is below the configured minimum. The message
            is byte-identical to the legacy inline guard so callers and tests
            matching on "Insufficient disk space" keep working.
    """
    free_mb = shutil.disk_usage(target_dir).free // (1024 * 1024)
    if free_mb < min_mb:
        raise OSError(
            f"Insufficient disk space ({free_mb}MB free, "
            f"need {min_mb}MB). File NOT saved."
        )
