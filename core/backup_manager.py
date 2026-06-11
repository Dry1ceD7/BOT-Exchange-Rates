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

import hashlib
import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


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

    def _source_digest(self, filepath: str) -> str:
        """Short stable discriminator for a source file's identity.

        The bare stem collided across directories (``2025/ledger.xlsx`` vs
        ``2026/ledger.xlsx``) and across extensions (``.xlsx`` vs ``.xlsm``),
        merging different files' backup pools — restore could then silently
        overwrite a ledger with a DIFFERENT file's backup. The digest covers
        the resolved parent directory plus the original extension, so each
        source file owns its own pool. (sha1 is an identity tag here, not a
        security boundary.)
        """
        p = Path(filepath)
        try:
            parent = str(p.resolve().parent)
        except OSError:
            parent = str(p.parent)
        raw = f"{parent}|{p.suffix.lower()}"
        return hashlib.sha1(  # noqa: S324 — non-crypto identity tag
            raw.encode("utf-8"),
        ).hexdigest()[:8]

    def _generate_backup_name(self, filepath: str) -> str:
        """Generates a timestamped backup filename with unique separator."""
        timestamp = datetime.now().strftime(self._TS_FORMAT)
        return f"{self._get_backup_key(filepath)}__bak__{timestamp}.xlsx"

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
        """Collision-proof lookup key: ``{stem}__{source-digest}``.

        See :meth:`_source_digest` — the bare stem alone merged backup pools
        of identically named ledgers from different folders/extensions.
        Legacy (pre-digest) backups named ``{stem}__bak__...`` remain
        restorable via the fallbacks in :meth:`restore_latest` /
        :meth:`restore_specific`.
        """
        return f"{Path(filepath).stem}__{self._source_digest(filepath)}"

    def _legacy_backup_key(self, filepath: str) -> str:
        """The pre-digest key (bare stem) for legacy backup fallback."""
        return Path(filepath).stem

    @staticmethod
    def display_stem(key: str) -> str:
        """Human-readable stem for a backup key (digest stripped).

        ``ledger__a8f487f3`` -> ``ledger``; legacy bare-stem keys pass
        through unchanged. For UI labels only — never use the result to
        match files (that is what the full key is for).
        """
        return re.sub(r"__[0-9a-f]{8}$", "", key)

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
            # Legacy fallback: backups created before the digest key carry
            # only the bare stem. They cannot prove which directory or
            # extension they came from, so log the ambiguity.
            legacy = self._legacy_backup_key(filepath)
            matches = sorted(
                (
                    str(p)
                    for p in Path(self.backup_dir).glob(
                        f"{legacy}__bak__*.xlsx",
                    )
                ),
                reverse=True,
            )
            if matches:
                logger.warning(
                    "Restoring from a LEGACY (pre-digest) backup for %s — "
                    "legacy backups cannot prove their source directory/"
                    "extension; verify the restored content.",
                    Path(filepath).name,
                )

        if not matches:
            raise BackupError(
                f"No backup found for '{Path(filepath).name}'.\n"
                f"The file must have been processed at least once to have a backup."
            )

        return self._restore_from(filepath, matches[0])

    def restore_specific(self, filepath: str, backup_path: str) -> str:
        """
        Restore a SPECIFIC backup over the live file (Backup Browser path).

        Unlike :meth:`restore_latest`, the caller chooses exactly which
        timestamped backup to roll back to — enabling a browse/restore-by-date
        undo history rather than only "revert latest". Reuses the same
        integrity check + pre-revert snapshot machinery so a specific restore is
        as safe as the latest one.

        Args:
            filepath:    Absolute path to the live file to overwrite.
            backup_path: Absolute path to the chosen backup. Must be a backup
                         that actually belongs to ``filepath`` (matched on the
                         shared base key) — restoring an unrelated file's backup
                         is refused.

        Returns:
            Absolute path to the backup that was used for restoration.

        Raises:
            BackupError: If the backup does not belong to the file, is missing,
                         empty/unreadable, or the copy fails.
        """
        # Guard against restoring a backup that belongs to a DIFFERENT source
        # file (e.g. a caller passing the wrong path). The embedded key must
        # match the target file's key. Legacy (pre-digest) names match on the
        # bare stem only — allowed for restorability of old backups, but
        # logged because they cannot prove their source directory/extension.
        expected_key = self._get_backup_key(filepath)
        backup_name = Path(backup_path).name
        if not backup_name.startswith(f"{expected_key}__bak__"):
            legacy_prefix = f"{self._legacy_backup_key(filepath)}__bak__"
            if not backup_name.startswith(legacy_prefix):
                raise BackupError(
                    f"Backup '{backup_name}' does not belong to "
                    f"'{Path(filepath).name}'."
                )
            logger.warning(
                "Restoring LEGACY (pre-digest) backup '%s' over %s — "
                "ownership matched on the bare filename only.",
                backup_name, Path(filepath).name,
            )

        # Containment check: the chosen backup must resolve to a path INSIDE
        # the managed backups directory. A caller-supplied path that merely
        # carries a matching name (or traverses out via '..'/symlink) must
        # never be copied over the live file.
        try:
            resolved_backup = Path(backup_path).resolve()
            backups_root = Path(self.backup_dir).resolve()
        except OSError as e:
            raise BackupError(f"Backup path could not be resolved: {e}") from e
        if not resolved_backup.is_relative_to(backups_root):
            raise BackupError(
                f"Backup '{backup_name}' is outside the backups directory — "
                "refusing to restore."
            )

        if not Path(backup_path).exists():
            raise BackupError(f"Backup no longer exists: {backup_path}")

        return self._restore_from(filepath, backup_path)

    def _restore_from(self, filepath: str, backup_path: str) -> str:
        """Shared restore machinery: integrity check + pre-revert snapshot +
        copy. Used by both restore_latest and restore_specific so a chosen
        restore is exactly as safe as the latest one."""
        # Integrity check: backup must be a readable, non-empty .xlsx.
        try:
            if Path(backup_path).stat().st_size <= 0:
                raise BackupError(
                    f"Backup is empty, refusing to restore: {backup_path}"
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
            shutil.copy2(backup_path, filepath)
        except OSError as e:
            raise BackupError(f"Restore failed: {e}") from e

        return backup_path

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
        matches = sorted(
            (str(p) for p in Path(self.backup_dir).glob(glob_pattern)),
            reverse=True,
        )
        if filepath and not matches:
            # Legacy (pre-digest) backups: bare-stem names. Kept listable so
            # the GUI revert pre-check still finds them.
            legacy = self._legacy_backup_key(filepath)
            matches = sorted(
                (
                    str(p)
                    for p in Path(self.backup_dir).glob(
                        f"{legacy}__bak__*.xlsx",
                    )
                ),
                reverse=True,
            )
        return matches

    def inspect_backups(self, filepath: str = None) -> list[dict]:
        """List backups with lightweight metadata (NO workbook loading).

        Returns a list of dicts (newest first), each carrying::

            {
                "path":       absolute path to the backup file (str),
                "key":        base name (stem) of the original file (str),
                "timestamp":  embedded datetime, or None if unparsable,
                "size_bytes": file size in bytes (int), 0 if stat fails,
            }

        Featherweight by design: only ``stat()`` + filename parsing are used,
        so it is safe to call repeatedly on a 4GB-RAM target without ever
        opening a single .xlsx.

        Args:
            filepath: If provided, only inspect backups for this source file.
        """
        records = []
        for path in self.list_backups(filepath):
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = 0
            records.append(
                {
                    "path": path,
                    "key": self._key_from_backup(path),
                    "timestamp": self._parse_backup_timestamp(path),
                    "size_bytes": size,
                }
            )
        return records

    def list_grouped_backups(self) -> dict[str, list[dict]]:
        """Group every backup by its original file's base key (newest first).

        Powers the Backup Browser: one entry per original ledger, each mapping
        to the chronological list of its timestamped backups (metadata only, no
        workbook loading). Keys are sorted alphabetically; each value preserves
        the newest-first ordering of :meth:`inspect_backups`.
        """
        grouped: dict[str, list[dict]] = {}
        for record in self.inspect_backups():
            grouped.setdefault(record["key"], []).append(record)
        # Return a dict ordered by key so the browser lists files predictably.
        return {key: grouped[key] for key in sorted(grouped)}

    def _key_from_backup(self, backup_path: str) -> str:
        """Recover the original file's base key from a backup filename.

        ``ledger__bak__20260604_...`` -> ``ledger``. Falls back to the full
        stem for any path that does not match the backup pattern.
        """
        stem = Path(backup_path).stem
        marker = "__bak__"
        idx = stem.rfind(marker)
        return stem[:idx] if idx != -1 else stem
