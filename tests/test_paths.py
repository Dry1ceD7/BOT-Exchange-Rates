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


@pytest.fixture(autouse=True)
def _fresh_root_cache():
    """Clear the memoized root around every test (F58).

    Each test must see a freshly-computed root (they monkeypatch sys.frozen /
    the probe), and a frozen-mode tmp_path root must never leak into other
    test files via the process-wide memo.
    """
    paths._reset_root_cache_for_tests()
    yield
    paths._reset_root_cache_for_tests()


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
#  Memoization (F58) — resolve once, never re-probe mid-session
# ───────────────────────────────────────────────────────────────────────
def test_memoized_root_does_not_reprobe(monkeypatch, tmp_path):
    """Repeated calls return the same object; the probe runs exactly once."""
    exe = tmp_path / "BOT-ExRate.exe"
    exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    calls = []
    monkeypatch.setattr(paths, "_is_writable", lambda d: calls.append(d) or True)

    first = paths.get_project_root()
    second = paths.get_project_root()
    third = paths.get_project_root()
    assert first == str(tmp_path)
    assert second is first
    assert third is first
    assert len(calls) == 1


def test_memoized_root_survives_writability_flip(monkeypatch, tmp_path):
    """A mid-session writability flip must NOT move the root (no split data/)."""
    exe = tmp_path / "BOT-ExRate.exe"
    exe.write_text("x")
    fallback = str(tmp_path / "userdata" / "BOT_Exrate")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(paths, "_is_writable", lambda d: True)
    monkeypatch.setattr(paths, "_user_data_root", lambda: fallback)

    first = paths.get_project_root()
    assert first == str(tmp_path)
    # Network-share hiccup: probe would now fail — root must stay put.
    monkeypatch.setattr(paths, "_is_writable", lambda d: False)
    assert paths.get_project_root() is first


def test_reset_seam_recomputes_root(monkeypatch, tmp_path):
    """_reset_root_cache_for_tests() forces a fresh probe and resolution."""
    exe = tmp_path / "BOT-ExRate.exe"
    exe.write_text("x")
    fallback = str(tmp_path / "userdata" / "BOT_Exrate")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(paths, "_user_data_root", lambda: fallback)

    monkeypatch.setattr(paths, "_is_writable", lambda d: True)
    assert paths.get_project_root() == str(tmp_path)

    paths._reset_root_cache_for_tests()
    monkeypatch.setattr(paths, "_is_writable", lambda d: False)
    assert paths.get_project_root() == fallback


def test_frozen_root_choice_logged_once(monkeypatch, tmp_path, caplog):
    """Frozen mode logs the chosen root exactly once (on first resolution)."""
    exe = tmp_path / "BOT-ExRate.exe"
    exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(paths, "_is_writable", lambda d: True)

    with caplog.at_level("INFO", logger="core.paths"):
        paths.get_project_root()
        paths.get_project_root()
    records = [r for r in caplog.records if "project root" in r.getMessage()]
    assert len(records) == 1
    assert str(tmp_path) in records[0].getMessage()


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
