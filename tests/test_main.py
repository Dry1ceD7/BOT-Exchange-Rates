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


def test_headless_run_not_blocked_by_running_gui(monkeypatch):
    """A headless run proceeds even when a GUI instance is already running.

    Regression: the single-instance guard used to run BEFORE the headless
    branch, so a scheduled `--headless` run would ping the open GUI, print
    'Another instance is already running' and exit 0 without processing any
    files — silently breaking the unattended workflow.
    """
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--headless"])

    # A GUI instance IS running — ping would succeed if it were consulted.
    ping_calls: list = []

    def _fake_ping():
        ping_calls.append(True)
        return True

    monkeypatch.setattr("core.ipc.ping_running_instance", _fake_ping)

    headless_calls: list = []
    monkeypatch.setattr(
        main, "_run_headless", lambda args: headless_calls.append(args),
    )

    main.main()

    # Headless path ran; the IPC guard was never consulted.
    assert len(headless_calls) == 1
    assert headless_calls[0].headless is True
    assert ping_calls == []


def test_gui_launch_blocked_by_running_instance(monkeypatch):
    """A GUI launch still defers to a running instance via the IPC guard."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    monkeypatch.setattr("core.ipc.ping_running_instance", lambda: True)

    # If the guard fails to short-circuit, these would be reached and blow up.
    def _should_not_run(*args, **kwargs):
        raise AssertionError("GUI launch proceeded past the single-instance guard")

    monkeypatch.setattr(main, "_run_headless", _should_not_run)
    monkeypatch.setattr(main, "_tokens_present", _should_not_run)

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 0


def test_gui_launch_proceeds_when_no_other_instance(monkeypatch):
    """With no running instance, GUI launch passes the guard and checks tokens."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    monkeypatch.setattr("core.ipc.ping_running_instance", lambda: False)

    token_checks: list = []

    def _no_tokens():
        token_checks.append(True)
        return False

    # No tokens present and the prompt is declined → clean exit(0) without
    # ever importing/constructing the CTk GUI app.
    monkeypatch.setattr(main, "_tokens_present", _no_tokens)
    monkeypatch.setattr(main, "_prompt_for_tokens", lambda: False)

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 0
    # The guard was passed (token check was reached).
    assert token_checks == [True]
