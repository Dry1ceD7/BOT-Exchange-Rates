#!/usr/bin/env python3
"""
tests/test_workbook_io.py
---------------------------------------------------------------------------
Unit tests for core/workbook_io.py — the in-place save guardrails.
Covers ensure_disk_space (free-space guard) and atomic_save (crash-safe
in-place overwrite via temp file + os.replace).
---------------------------------------------------------------------------
"""

from collections import namedtuple

import openpyxl
import pytest

import core.workbook_io as workbook_io_mod
from core.workbook_io import atomic_save, ensure_disk_space

_DiskUsage = namedtuple("_DiskUsage", ["total", "used", "free"])


# =========================================================================
#  ensure_disk_space
# =========================================================================

class TestEnsureDiskSpace:

    def test_passes_when_space_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            workbook_io_mod.shutil, "disk_usage",
            lambda _p: _DiskUsage(total=10**12, used=0, free=10**12),
        )
        # Plenty of space → no raise.
        ensure_disk_space(tmp_path, 100)

    def test_raises_when_space_low(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            workbook_io_mod.shutil, "disk_usage",
            lambda _p: _DiskUsage(total=10**12, used=10**12, free=0),
        )
        with pytest.raises(OSError, match="Insufficient disk space"):
            ensure_disk_space(tmp_path, 100)


# =========================================================================
#  atomic_save
# =========================================================================

class TestAtomicSave:

    def _seed_workbook(self, path, marker):
        """Write a small workbook with a sentinel value to a path."""
        wb = openpyxl.Workbook()
        wb.active["A1"] = marker
        wb.save(str(path))
        wb.close()

    def test_success_replaces_content(self, tmp_path):
        target = tmp_path / "ledger.xlsx"
        self._seed_workbook(target, "ORIGINAL")

        # New in-memory workbook with fresh content.
        wb = openpyxl.Workbook()
        wb.active["A1"] = "UPDATED"
        atomic_save(wb, str(target))
        wb.close()

        # Content swapped in.
        reloaded = openpyxl.load_workbook(str(target))
        assert reloaded.active["A1"].value == "UPDATED"
        reloaded.close()
        # No temp file left behind.
        assert not (tmp_path / "ledger.xlsx.tmp~").exists()

    def test_failure_leaves_original_unchanged(self, tmp_path):
        target = tmp_path / "ledger.xlsx"
        self._seed_workbook(target, "ORIGINAL")
        with open(target, "rb") as fh:
            original_bytes = fh.read()

        class _BoomWorkbook:
            """A workbook whose save() raises AFTER writing the temp file,
            simulating a crash mid-save."""
            def save(self, path):
                # Touch the temp file (as a real save would start to) then fail.
                with open(path, "wb") as fh:
                    fh.write(b"PARTIAL")
                raise OSError("simulated mid-save crash")

        with pytest.raises(OSError, match="simulated mid-save crash"):
            atomic_save(_BoomWorkbook(), str(target))

        # Original on disk is byte-for-byte intact — os.replace never ran.
        with open(target, "rb") as fh:
            assert fh.read() == original_bytes
        # The partial temp file was cleaned up.
        assert not (tmp_path / "ledger.xlsx.tmp~").exists()
