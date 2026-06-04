#!/usr/bin/env python3
"""
tests/test_backup_manager.py
---------------------------------------------------------------------------
Unit tests for core/backup_manager.py — BackupManager lifecycle operations.
---------------------------------------------------------------------------
"""

import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta

import pytest

from core.backup_manager import BackupError, BackupManager


@pytest.fixture
def backup_env():
    """
    Creates a temporary backup directory and a test .xlsx file.
    Yields (manager, test_filepath, backup_dir).
    Cleans up everything after the test.
    """
    tmpdir = tempfile.mkdtemp()
    backup_dir = os.path.join(tmpdir, "backups")
    os.makedirs(backup_dir)

    # Create a dummy xlsx file (just bytes, not a real spreadsheet)
    test_file = os.path.join(tmpdir, "ledger.xlsx")
    with open(test_file, "wb") as f:
        f.write(b"FAKE_XLSX_CONTENT_ORIGINAL")

    mgr = BackupManager(backup_dir=backup_dir)
    yield mgr, test_file, backup_dir, tmpdir
    shutil.rmtree(tmpdir)


# =========================================================================
#  CREATE BACKUP
# =========================================================================

class TestCreateBackup:
    """Tests for the create_backup method."""

    def test_creates_backup_file(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        backup_path = mgr.create_backup(test_file)
        assert os.path.exists(backup_path)
        assert backup_dir in backup_path

    def test_backup_uses_bak_separator(self, backup_env):
        mgr, test_file, _, _ = backup_env
        backup_path = mgr.create_backup(test_file)
        assert "__bak__" in os.path.basename(backup_path)

    def test_backup_content_matches_original(self, backup_env):
        mgr, test_file, _, _ = backup_env
        backup_path = mgr.create_backup(test_file)
        with open(backup_path, "rb") as f:
            assert f.read() == b"FAKE_XLSX_CONTENT_ORIGINAL"

    def test_backup_of_nonexistent_file_raises(self, backup_env):
        mgr, _, _, _ = backup_env
        with pytest.raises(BackupError, match="Source file not found"):
            mgr.create_backup("/nonexistent/path.xlsx")


# =========================================================================
#  RESTORE LATEST
# =========================================================================

class TestRestoreLatest:
    """Tests for the restore_latest method."""

    def test_restore_replaces_modified_file(self, backup_env):
        mgr, test_file, _, _ = backup_env
        # Create backup of original
        mgr.create_backup(test_file)

        # Modify the original file (simulate corruption)
        with open(test_file, "wb") as f:
            f.write(b"CORRUPTED_DATA")

        # Restore
        mgr.restore_latest(test_file)

        # Verify restored content
        with open(test_file, "rb") as f:
            assert f.read() == b"FAKE_XLSX_CONTENT_ORIGINAL"

    def test_restore_returns_backup_path(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        mgr.create_backup(test_file)
        result = mgr.restore_latest(test_file)
        assert backup_dir in result
        assert "__bak__" in os.path.basename(result)

    def test_restore_uses_latest_backup(self, backup_env):
        mgr, test_file, _, _ = backup_env

        # Create first backup with original content
        mgr.create_backup(test_file)
        time.sleep(1.1)  # Ensure different timestamp

        # Modify and create second backup
        with open(test_file, "wb") as f:
            f.write(b"UPDATED_CONTENT")
        mgr.create_backup(test_file)

        # Now corrupt the file
        with open(test_file, "wb") as f:
            f.write(b"CORRUPTED")

        # Restore should use the LATEST backup (UPDATED_CONTENT)
        mgr.restore_latest(test_file)
        with open(test_file, "rb") as f:
            assert f.read() == b"UPDATED_CONTENT"

    def test_restore_no_backup_raises(self, backup_env):
        mgr, _, _, tmpdir = backup_env
        no_backup_file = os.path.join(tmpdir, "unprocessed.xlsx")
        with open(no_backup_file, "wb") as f:
            f.write(b"DATA")
        with pytest.raises(BackupError, match="No backup found"):
            mgr.restore_latest(no_backup_file)


# =========================================================================
#  NO COLLISION (the __bak__ fix)
# =========================================================================

class TestNoCollision:
    """Tests that __bak__ separator prevents cross-file restoration."""

    def test_similar_names_do_not_collide(self, backup_env):
        mgr, _, _, tmpdir = backup_env

        # Create two similarly named files
        file_a = os.path.join(tmpdir, "ledger.xlsx")
        file_b = os.path.join(tmpdir, "ledger_2025.xlsx")
        with open(file_a, "wb") as f:
            f.write(b"FILE_A_DATA")
        with open(file_b, "wb") as f:
            f.write(b"FILE_B_DATA")

        # Backup both
        mgr.create_backup(file_a)
        mgr.create_backup(file_b)

        # Corrupt file_a
        with open(file_a, "wb") as f:
            f.write(b"CORRUPTED")

        # Restore file_a — should NOT get file_b's backup
        mgr.restore_latest(file_a)
        with open(file_a, "rb") as f:
            assert f.read() == b"FILE_A_DATA"

    def test_list_backups_filters_correctly(self, backup_env):
        mgr, _, _, tmpdir = backup_env

        file_a = os.path.join(tmpdir, "ledger.xlsx")
        file_b = os.path.join(tmpdir, "ledger_2025.xlsx")
        with open(file_a, "wb") as f:
            f.write(b"A")
        with open(file_b, "wb") as f:
            f.write(b"B")

        mgr.create_backup(file_a)
        mgr.create_backup(file_b)

        # list_backups for file_a should only return file_a's backup
        a_backups = mgr.list_backups(file_a)
        assert len(a_backups) == 1
        assert "ledger__bak__" in os.path.basename(a_backups[0])

        b_backups = mgr.list_backups(file_b)
        assert len(b_backups) == 1
        assert "ledger_2025__bak__" in os.path.basename(b_backups[0])


# =========================================================================
#  CLEANUP
# =========================================================================

class TestCleanup:
    """Tests for the cleanup_old_backups method."""

    def _write_backup_with_timestamp(self, backup_dir, base, dt):
        """Create a backup-named file whose embedded timestamp is dt."""
        ts = dt.strftime(BackupManager._TS_FORMAT)
        name = f"{base}__bak__{ts}.xlsx"
        path = os.path.join(backup_dir, name)
        with open(path, "wb") as f:
            f.write(b"BACKUP")
        return path

    def test_cleanup_removes_old_files(self, backup_env):
        mgr, _, backup_dir, _ = backup_env
        # Embedded timestamp 8 days ago -> should be deleted.
        old_dt = datetime.now() - timedelta(days=8)
        backup_path = self._write_backup_with_timestamp(backup_dir, "ledger", old_dt)

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 1
        assert not os.path.exists(backup_path)

    def test_cleanup_keeps_recent_files(self, backup_env):
        mgr, test_file, _, _ = backup_env
        backup_path = mgr.create_backup(test_file)

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 0
        assert os.path.exists(backup_path)

    def test_cleanup_ignores_stale_mtime_on_fresh_backup(self, backup_env):
        """REGRESSION (fix #1): a fresh backup of an OLD-mtime source must
        survive cleanup. copy2 preserves the source mtime, so an mtime-based
        cleanup would wrongly delete a just-created pristine copy.
        """
        mgr, test_file, _, _ = backup_env

        # Make the SOURCE file look months old.
        old_time = time.time() - (90 * 86400)
        os.utime(test_file, (old_time, old_time))

        # Fresh backup inherits the source's old mtime via copy2.
        backup_path = mgr.create_backup(test_file)
        assert os.path.getmtime(backup_path) < time.time() - (60 * 86400)

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 0
        assert os.path.exists(backup_path)

    def test_cleanup_skips_non_backup_xlsx(self, backup_env):
        """Files in the backup dir that don't match the __bak__ pattern are
        left untouched (no accidental deletion via the broad *.xlsx glob).
        """
        mgr, _, backup_dir, _ = backup_env
        stray = os.path.join(backup_dir, "not_a_backup.xlsx")
        with open(stray, "wb") as f:
            f.write(b"DATA")

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 0
        assert os.path.exists(stray)


# =========================================================================
#  COLLISION / INTEGRITY (fixes #2 and #3)
# =========================================================================

class TestTimestampGranularity:
    """Fix #2: backups within the same second must not collide."""

    def test_rapid_backups_unique_names(self, backup_env):
        mgr, test_file, _, _ = backup_env
        names = {mgr.create_backup(test_file) for _ in range(5)}
        assert len(names) == 5

    def test_rapid_backups_preserve_distinct_content(self, backup_env):
        mgr, test_file, _, _ = backup_env
        mgr.create_backup(test_file)  # pristine
        with open(test_file, "wb") as f:
            f.write(b"SECOND_VERSION")
        mgr.create_backup(test_file)
        # Both backups should still exist (no overwrite within the second).
        assert len(mgr.list_backups(test_file)) == 2


class TestRestoreIntegrity:
    """Fix #3: integrity check + pre-revert snapshot."""

    def test_restore_creates_pre_revert_snapshot(self, backup_env):
        mgr, test_file, _, _ = backup_env
        mgr.create_backup(test_file)
        with open(test_file, "wb") as f:
            f.write(b"LIVE_BEFORE_REVERT")

        mgr.restore_latest(test_file)

        pre = test_file + ".pre-revert"
        assert os.path.exists(pre)
        with open(pre, "rb") as f:
            assert f.read() == b"LIVE_BEFORE_REVERT"

    def test_restore_rejects_empty_backup(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        # Create a zero-byte backup that matches the pattern.
        ts = datetime.now().strftime(BackupManager._TS_FORMAT)
        empty = os.path.join(backup_dir, f"ledger__bak__{ts}.xlsx")
        open(empty, "wb").close()

        with pytest.raises(BackupError, match="empty"):
            mgr.restore_latest(test_file)


# =========================================================================
#  BACKUP BROWSER API (inspect / grouped metadata + restore-by-selection)
# =========================================================================

class TestInspectBackups:
    """inspect_backups returns metadata only — no workbook loading."""

    def test_inspect_returns_metadata_fields(self, backup_env):
        mgr, test_file, _, _ = backup_env
        mgr.create_backup(test_file)
        records = mgr.inspect_backups(test_file)
        assert len(records) == 1
        rec = records[0]
        assert set(rec) == {"path", "key", "timestamp", "size_bytes"}
        assert rec["key"] == "ledger"
        assert isinstance(rec["timestamp"], datetime)
        # FAKE_XLSX_CONTENT_ORIGINAL is 26 bytes.
        assert rec["size_bytes"] == len(b"FAKE_XLSX_CONTENT_ORIGINAL")

    def test_inspect_newest_first(self, backup_env):
        mgr, test_file, _, _ = backup_env
        mgr.create_backup(test_file)
        time.sleep(1.1)
        with open(test_file, "wb") as f:
            f.write(b"NEWER")
        newest = mgr.create_backup(test_file)
        records = mgr.inspect_backups(test_file)
        assert len(records) == 2
        assert records[0]["path"] == newest

    def test_inspect_all_when_no_filter(self, backup_env):
        mgr, _, _, tmpdir = backup_env
        for name, data in (("a.xlsx", b"A"), ("b.xlsx", b"B")):
            p = os.path.join(tmpdir, name)
            with open(p, "wb") as f:
                f.write(data)
            mgr.create_backup(p)
        records = mgr.inspect_backups()
        assert len(records) == 2

    def test_inspect_ignores_unparsable_timestamp(self, backup_env):
        """A stray __bak__ file with a bad timestamp still lists (ts=None)."""
        mgr, _, backup_dir, _ = backup_env
        stray = os.path.join(backup_dir, "weird__bak__NOTATIME.xlsx")
        with open(stray, "wb") as f:
            f.write(b"X")
        records = mgr.inspect_backups()
        assert any(r["timestamp"] is None for r in records)


class TestGroupedBackups:
    """list_grouped_backups groups by original file key."""

    def test_groups_by_key(self, backup_env):
        mgr, _, _, tmpdir = backup_env
        file_a = os.path.join(tmpdir, "alpha.xlsx")
        file_b = os.path.join(tmpdir, "beta.xlsx")
        with open(file_a, "wb") as f:
            f.write(b"A")
        with open(file_b, "wb") as f:
            f.write(b"B")
        mgr.create_backup(file_a)
        mgr.create_backup(file_a)
        mgr.create_backup(file_b)

        grouped = mgr.list_grouped_backups()
        assert set(grouped) == {"alpha", "beta"}
        assert len(grouped["alpha"]) == 2
        assert len(grouped["beta"]) == 1

    def test_keys_sorted(self, backup_env):
        mgr, _, _, tmpdir = backup_env
        for name in ("zeta.xlsx", "alpha.xlsx", "mid.xlsx"):
            p = os.path.join(tmpdir, name)
            with open(p, "wb") as f:
                f.write(b"X")
            mgr.create_backup(p)
        grouped = mgr.list_grouped_backups()
        assert list(grouped) == ["alpha", "mid", "zeta"]

    def test_empty_when_no_backups(self, backup_env):
        mgr, _, _, _ = backup_env
        assert mgr.list_grouped_backups() == {}


class TestRestoreSpecific:
    """restore_specific rolls back to a CHOSEN backup, safely."""

    def test_restore_earlier_backup(self, backup_env):
        mgr, test_file, _, _ = backup_env
        # Backup 1: ORIGINAL.
        first = mgr.create_backup(test_file)
        time.sleep(1.1)
        # Backup 2: UPDATED.
        with open(test_file, "wb") as f:
            f.write(b"UPDATED_CONTENT")
        mgr.create_backup(test_file)
        # Live file is now corrupted.
        with open(test_file, "wb") as f:
            f.write(b"CORRUPTED")

        # Restore the EARLIER backup specifically (not the latest).
        used = mgr.restore_specific(test_file, first)
        assert used == first
        with open(test_file, "rb") as f:
            assert f.read() == b"FAKE_XLSX_CONTENT_ORIGINAL"

    def test_restore_specific_creates_pre_revert_snapshot(self, backup_env):
        mgr, test_file, _, _ = backup_env
        backup = mgr.create_backup(test_file)
        with open(test_file, "wb") as f:
            f.write(b"LIVE_BEFORE")
        mgr.restore_specific(test_file, backup)
        pre = test_file + ".pre-revert"
        assert os.path.exists(pre)
        with open(pre, "rb") as f:
            assert f.read() == b"LIVE_BEFORE"

    def test_restore_specific_rejects_foreign_backup(self, backup_env):
        """A backup that belongs to a DIFFERENT file is refused."""
        mgr, test_file, _, tmpdir = backup_env
        other = os.path.join(tmpdir, "other.xlsx")
        with open(other, "wb") as f:
            f.write(b"OTHER")
        other_backup = mgr.create_backup(other)
        with pytest.raises(BackupError, match="does not belong"):
            mgr.restore_specific(test_file, other_backup)

    def test_restore_specific_missing_backup_raises(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        ghost = os.path.join(backup_dir, "ledger__bak__20200101_000000_000000.xlsx")
        with pytest.raises(BackupError, match="no longer exists"):
            mgr.restore_specific(test_file, ghost)

    def test_restore_specific_rejects_empty_backup(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        ts = datetime.now().strftime(BackupManager._TS_FORMAT)
        empty = os.path.join(backup_dir, f"ledger__bak__{ts}.xlsx")
        open(empty, "wb").close()
        with pytest.raises(BackupError, match="empty"):
            mgr.restore_specific(test_file, empty)
