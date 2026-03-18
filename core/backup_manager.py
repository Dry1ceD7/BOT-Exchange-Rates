#!/usr/bin/env python3
"""
core/backup_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.0) - Fail-Safe Backup & Revert
---------------------------------------------------------------------------
Lightweight backup/restore layer using only Python stdlib (shutil, os, glob).
Protects against file corruption during in-place editing with automatic
garbage collection for legacy hardware.
"""

import os
import shutil
import glob
from datetime import datetime, timedelta


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
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            backup_dir = os.path.join(project_root, "data", "backups")

        self.backup_dir = backup_dir
        os.makedirs(self.backup_dir, exist_ok=True)

    def _generate_backup_name(self, filepath: str) -> str:
        """Generates a timestamped backup filename with unique separator."""
        basename = os.path.splitext(os.path.basename(filepath))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{basename}__bak__{timestamp}.xlsx"

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
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
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
