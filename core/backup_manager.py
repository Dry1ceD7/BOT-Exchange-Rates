#!/usr/bin/env python3
"""
core/backup_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.6.1) - Fail-Safe Backup & Revert
---------------------------------------------------------------------------
Lightweight backup/restore layer using only Python stdlib (shutil, os, glob).
Protects against file corruption during in-place editing with automatic
garbage collection for legacy hardware.
"""

import glob
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional


class BackupError(Exception):
    """Raised when a backup or restore operation fails."""
    pass


class BackupManager:
    """
    Manages timestamped backups of Excel files in a local data/backups/ directory.
    Designed to be featherweight — zero external dependencies.
    """

    def __init__(self, backup_dir: str = None):
        """
        Args:
            backup_dir: Absolute path to backup directory.
                        Defaults to <project_root>/data/backups/
        """
        if backup_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            backup_dir = os.path.join(project_root, "data", "backups")

        self.backup_dir = backup_dir
        os.makedirs(self.backup_dir, exist_ok=True)

    # Timestamp embedded in backup filenames. Microsecond precision avoids
    # same-second collisions that would overwrite a pristine copy.
    _TS_FORMAT = "%Y%m%d_%H%M%S_%f"

    def _generate_backup_name(self, filepath: str) -> str:
        """Generates a timestamped backup filename with unique separator."""
        basename = os.path.splitext(os.path.basename(filepath))[0]
        timestamp = datetime.now().strftime(self._TS_FORMAT)
        return f"{basename}__bak__{timestamp}.xlsx"

    def _parse_backup_timestamp(self, fpath: str) -> Optional[datetime]:
        """Extract the embedded timestamp from a backup filename.

        Returns None if the filename does not match the backup pattern
        (so non-backup *.xlsx files are safely ignored).
        """
        name = os.path.splitext(os.path.basename(fpath))[0]
        marker = "__bak__"
        idx = name.rfind(marker)
        if idx == -1:
            return None
        ts_str = name[idx + len(marker):]
        try:
            return datetime.strptime(ts_str, self._TS_FORMAT)
        except ValueError:
            return None

    def _get_backup_key(self, filepath: str) -> str:
        """Extracts the base name (without extension) used as a lookup key."""
        return os.path.splitext(os.path.basename(filepath))[0]

    def create_backup(self, filepath: str) -> str:
        """
        Creates a pristine backup copy of the file BEFORE any modifications.

        Args:
            filepath: Absolute path to the original .xlsx file.

        Returns:
            Absolute path to the backup file.

        Raises:
            BackupError: If the copy operation fails.
        """
        if not os.path.exists(filepath):
            raise BackupError(f"Source file not found: {filepath}")

        backup_name = self._generate_backup_name(filepath)
        backup_path = os.path.join(self.backup_dir, backup_name)

        try:
            shutil.copy2(filepath, backup_path)
        except (OSError, IOError) as e:
            raise BackupError(f"Backup failed for {os.path.basename(filepath)}: {e}")

        return backup_path

    def restore_latest(self, filepath: str) -> str:
        """
        Finds the most recent backup matching the original filename and
        overwrites the (potentially corrupted) file with the pristine backup.

        Args:
            filepath: Absolute path to the file to be restored.

        Returns:
            Absolute path to the backup that was used for restoration.

        Raises:
            BackupError: If no matching backup is found.
        """
        key = self._get_backup_key(filepath)
        pattern = os.path.join(self.backup_dir, f"{key}__bak__*.xlsx")
        matches = sorted(glob.glob(pattern), reverse=True)

        if not matches:
            raise BackupError(
                f"No backup found for '{os.path.basename(filepath)}'.\n"
                f"The file must have been processed at least once to have a backup."
            )

        latest_backup = matches[0]

        # Integrity check: backup must be a readable, non-empty .xlsx.
        try:
            if os.path.getsize(latest_backup) <= 0:
                raise BackupError(
                    f"Backup is empty, refusing to restore: {latest_backup}"
                )
        except OSError as e:
            raise BackupError(f"Backup not readable: {e}")

        # Snapshot the current live file before overwriting so a bad revert
        # is recoverable.
        if os.path.exists(filepath):
            try:
                shutil.copy2(filepath, filepath + ".pre-revert")
            except (OSError, IOError) as e:
                raise BackupError(f"Pre-revert snapshot failed: {e}")

        try:
            shutil.copy2(latest_backup, filepath)
        except (OSError, IOError) as e:
            raise BackupError(f"Restore failed: {e}")

        return latest_backup

    def cleanup_old_backups(self, max_age_days: int = 7) -> int:
        """
        Deletes backup files older than max_age_days to preserve disk space
        on legacy 4GB RAM PCs.

        Args:
            max_age_days: Maximum age in days. Backups older than this are deleted.

        Returns:
            Number of files deleted.
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        deleted = 0

        for fpath in glob.glob(os.path.join(self.backup_dir, "*.xlsx")):
            # Derive age from the timestamp embedded in the filename, NOT
            # os.path.getmtime: copy2 preserves the SOURCE mtime, so a fresh
            # backup of an old file would otherwise be deleted immediately.
            ts = self._parse_backup_timestamp(fpath)
            if ts is None:
                # Not a recognizable backup file — skip it.
                continue
            if ts < cutoff:
                try:
                    os.remove(fpath)
                    deleted += 1
                except OSError:
                    continue

        return deleted

    def list_backups(self, filepath: str = None) -> list:
        """
        Lists all backup files, optionally filtered to a specific source file.

        Args:
            filepath: If provided, only list backups matching this file.

        Returns:
            List of backup file paths, newest first.
        """
        if filepath:
            key = self._get_backup_key(filepath)
            pattern = os.path.join(self.backup_dir, f"{key}__bak__*.xlsx")
        else:
            pattern = os.path.join(self.backup_dir, "*.xlsx")

        return sorted(glob.glob(pattern), reverse=True)
