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
    """SECURITY: the Sentry before_send scrubber replaces token values.

    Tokens are sourced via secure_tokens.get_token (keychain + .env), so we
    patch it to return the test tokens regardless of the host keychain state.
    """
    monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
    monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
    main = _import_main_with_fake_tk(monkeypatch)

    _tokens = {"BOT_TOKEN_EXG": "exgsecretAAA", "BOT_TOKEN_HOL": "holsecretBBB"}
    monkeypatch.setattr(
        "core.secure_tokens.get_token", lambda key: _tokens.get(key),
    )

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
    monkeypatch.setattr("core.secure_tokens.get_token", lambda key: None)
    event = {"message": "hello"}
    assert main._sentry_token_scrubber(event, {}) == event


def test_sentry_scrubber_redacts_keychain_sourced_token(monkeypatch):
    """SECURITY: scrubber redacts tokens sourced from the keychain (env empty).

    After the env→keychain migration os.environ is scrubbed, so the scrubber
    must consult secure_tokens.get_token to keep redacting.
    """
    monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
    monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)
    main = _import_main_with_fake_tk(monkeypatch)

    def _fake_get_token(env_key):
        return "keychainSecretXYZ" if env_key == "BOT_TOKEN_EXG" else None

    monkeypatch.setattr("core.secure_tokens.get_token", _fake_get_token)
    event = {"message": "boom keychainSecretXYZ here"}
    scrubbed = main._sentry_token_scrubber(event, {})
    assert "keychainSecretXYZ" not in str(scrubbed)
    assert "***" in scrubbed["message"]


def test_purge_credentials_deletes_both_tokens(monkeypatch, capsys):
    """--purge-credentials deletes both keychain tokens and reports a result."""
    main = _import_main_with_fake_tk(monkeypatch)
    deleted: list = []
    monkeypatch.setattr(
        "core.secure_tokens.delete_token",
        lambda env_key: deleted.append(env_key) or True,
    )
    main._purge_credentials()
    assert deleted == ["BOT_TOKEN_EXG", "BOT_TOKEN_HOL"]
    out = capsys.readouterr().out
    assert "Purged 2" in out
