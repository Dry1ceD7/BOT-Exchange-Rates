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

import shutil
from datetime import datetime, timedelta
from pathlib import Path


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
            # Keep backup_dir as a str: it is used downstream both as a
            # filesystem path and as a glob-pattern prefix (string ops).
            backup_dir = str(Path(project_root) / "data" / "backups")

        self.backup_dir = backup_dir
        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)

    # Timestamp embedded in backup filenames. Microsecond precision avoids
    # same-second collisions that would overwrite a pristine copy.
    _TS_FORMAT = "%Y%m%d_%H%M%S_%f"

    def _generate_backup_name(self, filepath: str) -> str:
        """Generates a timestamped backup filename with unique separator."""
        basename = Path(filepath).stem
        timestamp = datetime.now().strftime(self._TS_FORMAT)
        return f"{basename}__bak__{timestamp}.xlsx"

    def _parse_backup_timestamp(self, fpath: str) -> datetime | None:
        """Extract the embedded timestamp from a backup filename.

        Returns None if the filename does not match the backup pattern
        (so non-backup *.xlsx files are safely ignored).
        """
        name = Path(fpath).stem
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
        return Path(filepath).stem

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
        if not Path(filepath).exists():
            raise BackupError(f"Source file not found: {filepath}")

        backup_name = self._generate_backup_name(filepath)
        # Keep backup_path as str: returned to callers (engine) and matched
        # later by glob patterns built via string concatenation.
        backup_path = str(Path(self.backup_dir) / backup_name)

        try:
            shutil.copy2(filepath, backup_path)
        except OSError as e:
            raise BackupError(f"Backup failed for {Path(filepath).name}: {e}") from e

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
        # Sort the matched paths as strings (reverse) to preserve the exact
        # lexicographic ordering the old glob.glob + sorted produced.
        matches = sorted(
            (str(p) for p in Path(self.backup_dir).glob(f"{key}__bak__*.xlsx")),
            reverse=True,
        )

        if not matches:
            raise BackupError(
                f"No backup found for '{Path(filepath).name}'.\n"
                f"The file must have been processed at least once to have a backup."
            )

        latest_backup = matches[0]

        # Integrity check: backup must be a readable, non-empty .xlsx.
        try:
            if Path(latest_backup).stat().st_size <= 0:
                raise BackupError(
                    f"Backup is empty, refusing to restore: {latest_backup}"
                )
        except OSError as e:
            raise BackupError(f"Backup not readable: {e}") from e

        # Snapshot the current live file before overwriting so a bad revert
        # is recoverable.
        if Path(filepath).exists():
            try:
                shutil.copy2(filepath, filepath + ".pre-revert")
            except OSError as e:
                raise BackupError(f"Pre-revert snapshot failed: {e}") from e

        try:
            shutil.copy2(latest_backup, filepath)
        except OSError as e:
            raise BackupError(f"Restore failed: {e}") from e

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

        for fpath in Path(self.backup_dir).glob("*.xlsx"):
            # Derive age from the timestamp embedded in the filename, NOT
            # st_mtime: copy2 preserves the SOURCE mtime, so a fresh
            # backup of an old file would otherwise be deleted immediately.
            ts = self._parse_backup_timestamp(fpath)
            if ts is None:
                # Not a recognizable backup file — skip it.
                continue
            if ts < cutoff:
                try:
                    fpath.unlink()
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
            glob_pattern = f"{key}__bak__*.xlsx"
        else:
            glob_pattern = "*.xlsx"

        # Return str paths (newest first) to match the previous glob.glob
        # contract: callers display/compare these as strings.
        return sorted(
            (str(p) for p in Path(self.backup_dir).glob(glob_pattern)),
            reverse=True,
        )
