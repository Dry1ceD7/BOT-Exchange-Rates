#!/usr/bin/env python3
"""
tests/test_backup_manager.py
---------------------------------------------------------------------------
Unit tests for core/backup_manager.py — BackupManager lifecycle operations.
---------------------------------------------------------------------------
"""

import os
import time
import pytest
import tempfile
import shutil
from core.backup_manager import BackupManager, BackupError


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

    def test_cleanup_removes_old_files(self, backup_env):
        mgr, test_file, backup_dir, _ = backup_env
        backup_path = mgr.create_backup(test_file)

        # Set the backup's mtime to 8 days ago
        old_time = time.time() - (8 * 86400)
        os.utime(backup_path, (old_time, old_time))

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 1
        assert not os.path.exists(backup_path)

    def test_cleanup_keeps_recent_files(self, backup_env):
        mgr, test_file, _, _ = backup_env
        backup_path = mgr.create_backup(test_file)

        deleted = mgr.cleanup_old_backups(max_age_days=7)
        assert deleted == 0
        assert os.path.exists(backup_path)
