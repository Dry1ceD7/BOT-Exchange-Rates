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

import contextlib
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


def atomic_save(wb, filepath: str) -> None:
    """Save an openpyxl workbook atomically over an existing file.

    ``wb.save(filepath)`` opens the target ZipFile in 'w' mode, truncating the
    original immediately — a crash mid-save destroys the ledger. We instead
    save to a sibling temp file in the SAME directory (so the replace stays on
    one filesystem and is atomic) and only swap it in once the write fully
    succeeds. On any failure the original is left untouched and the temp file
    is removed.

    Args:
        wb: An openpyxl Workbook with a ``save(path)`` method.
        filepath: Destination path to overwrite atomically.
    """
    target = Path(filepath)
    tmp_path = target.with_name(f"{target.name}.tmp~")
    try:
        wb.save(str(tmp_path))
        # Path.replace is atomic on the same filesystem (same dir guarantees it).
        tmp_path.replace(target)
    except BaseException:
        # Clean up the partial temp file; never leave it behind. The original
        # file is still intact because the replace never ran.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
