#!/usr/bin/env python3
"""
tests/test_paths.py
---------------------------------------------------------------------------
Tests for core/paths.py — dev-mode path stability and the frozen-mode
writable-fallback decision.
"""

import os
import stat
import sys

import pytest

import core.paths as paths


# ───────────────────────────────────────────────────────────────────────
#  Dev mode must be unchanged
# ───────────────────────────────────────────────────────────────────────
def test_dev_mode_path_unchanged(monkeypatch):
    """In source (non-frozen) mode the root is the repo dir containing core/."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
    assert paths.get_project_root() == expected


def test_dev_mode_ignores_writability(monkeypatch):
    """Dev mode must not consult the writability probe / fallback at all."""
    monkeypatch.delattr(sys, "frozen", raising=False)

    def _boom(_):
        raise AssertionError("_is_writable must not run in dev mode")

    monkeypatch.setattr(paths, "_is_writable", _boom)
    monkeypatch.setattr(paths, "_user_data_root", _boom)
    expected = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
    assert paths.get_project_root() == expected


# ───────────────────────────────────────────────────────────────────────
#  Frozen mode — writable exe dir keeps exe-dir default
# ───────────────────────────────────────────────────────────────────────
def test_frozen_writable_uses_exe_dir(monkeypatch, tmp_path):
    exe = tmp_path / "BOT-ExRate.exe"
    exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(paths, "_is_writable", lambda d: True)
    # Fallback must NOT be used when exe dir is writable.
    monkeypatch.setattr(
        paths, "_user_data_root",
        lambda: (_ for _ in ()).throw(AssertionError("fallback used")),
    )
    assert paths.get_project_root() == str(tmp_path)


# ───────────────────────────────────────────────────────────────────────
#  Frozen mode — non-writable exe dir falls back to per-user data dir
# ───────────────────────────────────────────────────────────────────────
def test_frozen_non_writable_falls_back(monkeypatch, tmp_path):
    exe = tmp_path / "ProgramFiles" / "BOT-ExRate.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    fallback = str(tmp_path / "userdata" / "BOT_Exrate")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(paths, "_is_writable", lambda d: False)
    monkeypatch.setattr(paths, "_user_data_root", lambda: fallback)
    assert paths.get_project_root() == fallback


# ───────────────────────────────────────────────────────────────────────
#  Writability probe
# ───────────────────────────────────────────────────────────────────────
def test_is_writable_true_for_tmp(tmp_path):
    assert paths._is_writable(str(tmp_path / "sub")) is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod semantics")
def test_is_writable_false_for_readonly(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)  # r-x, no write
    try:
        assert paths._is_writable(str(ro)) is False
    finally:
        os.chmod(ro, 0o700)  # restore so pytest can clean up


# ───────────────────────────────────────────────────────────────────────
#  harden_data_dirs
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod semantics")
def test_harden_data_dirs_sets_0700(tmp_path):
    paths.harden_data_dirs(str(tmp_path))
    for sub in ("data", "data/logs", "data/backups"):
        target = tmp_path / sub
        assert target.is_dir()
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700


def test_harden_data_dirs_swallows_errors(tmp_path, monkeypatch):
    """OSError from chmod must be swallowed (e.g. unsupported FS)."""
    def _raise(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(paths.os, "chmod", _raise)
    # Should not raise.
    paths.harden_data_dirs(str(tmp_path))
