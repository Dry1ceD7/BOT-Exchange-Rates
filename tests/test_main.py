#!/usr/bin/env python3
"""Tests for main.py fatal exception logging behavior."""

import importlib
import os
import sys
import types


def _import_main_with_fake_tk(monkeypatch):
    """Import main.py while providing a minimal fake tkinter module."""
    fake_messagebox = types.SimpleNamespace(showerror=lambda *args, **kwargs: None)
    fake_tk_module = types.SimpleNamespace(
        Tk=lambda: types.SimpleNamespace(
            withdraw=lambda: None,
            destroy=lambda: None,
        ),
        messagebox=fake_messagebox,
    )
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk_module)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_messagebox)

    if "main" in sys.modules:
        del sys.modules["main"]
    return importlib.import_module("main")


def test_global_exception_handler_writes_error_log_to_data_logs(tmp_path, monkeypatch):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "get_project_root", lambda: str(tmp_path))

    main.global_exception_handler(RuntimeError, RuntimeError("boom"), None)

    expected_log = os.path.join(str(tmp_path), "data", "logs", "error.log")
    assert os.path.exists(expected_log)
    with open(expected_log, encoding="utf-8") as f:
        content = f.read()
    assert "--- FATAL ERROR ---" in content
    assert "RuntimeError: boom" in content


def test_sentry_scrubber_redacts_tokens(monkeypatch):
    """SECURITY: the Sentry before_send scrubber replaces token values."""
    monkeypatch.setenv("BOT_TOKEN_EXG", "exgsecretAAA")
    monkeypatch.setenv("BOT_TOKEN_HOL", "holsecretBBB")
    main = _import_main_with_fake_tk(monkeypatch)

    event = {
        "message": "failed with token exgsecretAAA",
        "extra": {"hdr": "Bearer holsecretBBB", "ok": "fine"},
        "list": ["exgsecretAAA", 1],
    }
    scrubbed = main._sentry_token_scrubber(event, {})
    assert "exgsecretAAA" not in str(scrubbed)
    assert "holsecretBBB" not in str(scrubbed)
    assert scrubbed["extra"]["ok"] == "fine"


def test_sentry_scrubber_noop_without_tokens(monkeypatch):
    """Scrubber returns event unchanged when no tokens are set."""
    monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
    monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
    main = _import_main_with_fake_tk(monkeypatch)
    event = {"message": "hello"}
    assert main._sentry_token_scrubber(event, {}) == event
