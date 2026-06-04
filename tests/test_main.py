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

    def _fake_headless(args):
        headless_calls.append(args)
        return main.EXIT_OK

    monkeypatch.setattr(main, "_run_headless", _fake_headless)

    # main() now propagates the headless exit-code contract via sys.exit.
    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == main.EXIT_OK

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

    # No tokens present and the prompt is declined → exit EXIT_CONFIG with a
    # clear message, without ever importing/constructing the CTk GUI app.
    monkeypatch.setattr(main, "_tokens_present", _no_tokens)
    monkeypatch.setattr(main, "_prompt_for_tokens", lambda: False)
    shown: list = []
    monkeypatch.setattr(
        main, "_show_tokens_required_message", lambda: shown.append(True),
    )

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == main.EXIT_CONFIG
    # The guard was passed (token check was reached) and the user was told why.
    assert token_checks == [True]
    assert shown == [True]


# ── First-run token dialog: explain before exiting ──────────────────────
def test_closing_token_dialog_shows_message_and_exits_config(monkeypatch):
    """Closing the first-run dialog explains why, then exits with EXIT_CONFIG.

    Regression: the app used to vanish silently (exit 0) when the user closed
    the registration dialog without entering keys — indistinguishable from a
    crash and from a clean shutdown.
    """
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr("core.ipc.ping_running_instance", lambda: False)
    monkeypatch.setattr(main, "_tokens_present", lambda: False)
    monkeypatch.setattr(main, "_prompt_for_tokens", lambda: False)

    shown: list = []
    monkeypatch.setattr(
        main, "_show_tokens_required_message", lambda: shown.append(True),
    )

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == main.EXIT_CONFIG
    assert shown == [True]


def test_show_tokens_required_message_falls_back_to_stderr(monkeypatch, capsys):
    """When no display is available, the reason is printed to stderr."""
    main = _import_main_with_fake_tk(monkeypatch)

    def _boom():
        raise RuntimeError("no display")

    # Force the Tk path to fail so the stderr fallback is exercised.
    monkeypatch.setattr(main.tk, "Tk", _boom)
    main._show_tokens_required_message()
    err = capsys.readouterr().err
    assert "API keys are required" in err
    assert "portal" in err


# ── Headless exit-code contract ──────────────────────────────────────────
def _stub_headless_engine(monkeypatch, main, *, success, fail, errors=None,
                          audit_path=None):
    """Stub the batch so no real BOTClient/network is constructed."""
    captured: dict = {}

    def _fake_batch(files, start_date, *, dry_run=False, quiet=False,
                    json_mode=False):
        captured["files"] = files
        captured["start_date"] = start_date
        captured["dry_run"] = dry_run
        return success, fail, errors or [], audit_path

    monkeypatch.setattr(main, "_process_headless_batch", _fake_batch)
    return captured


def _headless_args(main, **overrides):
    import argparse
    defaults = dict(
        headless=True, input=None, start_date="2025-01-02", dry_run=False,
        quiet=False, verbose=False, json=False, schedule=None,
        purge_credentials=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_headless_exit_ok_all_succeeded(monkeypatch, tmp_path):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    _stub_headless_engine(monkeypatch, main, success=3, fail=0)
    args = _headless_args(main, input=str(f))
    assert main._run_headless(args) == main.EXIT_OK


def test_headless_exit_total_failure(monkeypatch, tmp_path):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    _stub_headless_engine(monkeypatch, main, success=0, fail=2,
                          errors=["a: boom", "b: boom"])
    args = _headless_args(main, input=str(f))
    assert main._run_headless(args) == main.EXIT_TOTAL


def test_headless_exit_partial_failure(monkeypatch, tmp_path):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    _stub_headless_engine(monkeypatch, main, success=1, fail=1,
                          errors=["b: boom"])
    args = _headless_args(main, input=str(f))
    assert main._run_headless(args) == main.EXIT_PARTIAL


def test_headless_exit_config_missing_tokens(monkeypatch):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: False)
    args = _headless_args(main)
    assert main._run_headless(args) == main.EXIT_CONFIG


def test_headless_exit_config_bad_input_path(monkeypatch):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    args = _headless_args(main, input="/no/such/path/here")
    assert main._run_headless(args) == main.EXIT_CONFIG


def test_headless_exit_nothing_to_do_on_empty_folder(monkeypatch, tmp_path):
    """An empty input folder returns EXIT_NOTHING (not success exit 0)."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    empty = tmp_path / "input"
    empty.mkdir()
    args = _headless_args(main, input=str(empty))
    assert main._run_headless(args) == main.EXIT_NOTHING


# ── --dry-run flag ───────────────────────────────────────────────────────
def test_headless_dry_run_passes_flag_and_banner(monkeypatch, tmp_path, capsys):
    """--dry-run forwards dry_run=True to the engine and prints the banner."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    captured = _stub_headless_engine(monkeypatch, main, success=2, fail=0)
    args = _headless_args(main, input=str(f), dry_run=True)
    assert main._run_headless(args) == main.EXIT_OK
    assert captured["dry_run"] is True
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    # Dry run must not advertise an audit log as a destructive record.
    assert "Audit log" not in out


# ── --quiet / --verbose ──────────────────────────────────────────────────
def test_headless_quiet_suppresses_per_file_but_prints_summary(
    monkeypatch, tmp_path, capsys
):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    _stub_headless_engine(monkeypatch, main, success=1, fail=0)
    args = _headless_args(main, input=str(f), quiet=True)
    assert main._run_headless(args) == main.EXIT_OK
    out = capsys.readouterr().out
    # The "Found N file(s)" preamble is suppressed; the summary still prints.
    assert "Found" not in out
    assert "Results: 1 succeeded, 0 failed" in out


def test_set_console_log_level_only_touches_stream_handler(monkeypatch):
    """--verbose/--quiet adjust the console handler, never the file handler."""
    main = _import_main_with_fake_tk(monkeypatch)
    import logging as _logging

    root = _logging.getLogger()
    stream = _logging.StreamHandler()
    file_handler = _logging.FileHandler(os.devnull)
    root.addHandler(stream)
    root.addHandler(file_handler)
    try:
        file_handler.setLevel(_logging.INFO)
        main._set_console_log_level(_logging.DEBUG)
        assert stream.level == _logging.DEBUG
        # File handler must stay at INFO so app.log keeps the full trail.
        assert file_handler.level == _logging.INFO
    finally:
        root.removeHandler(stream)
        root.removeHandler(file_handler)
        file_handler.close()


# ── --json summary ───────────────────────────────────────────────────────
def test_headless_json_summary_is_machine_readable(monkeypatch, tmp_path, capsys):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    f = tmp_path / "ledger.xlsx"
    f.write_text("x")
    _stub_headless_engine(
        monkeypatch, main, success=2, fail=1, errors=["b.xlsx: boom"],
        audit_path="/data/audit.csv",
    )
    args = _headless_args(main, input=str(f), json=True)
    assert main._run_headless(args) == main.EXIT_PARTIAL
    import json as _json
    out = capsys.readouterr().out.strip()
    payload = _json.loads(out)
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    assert payload["errors"] == ["b.xlsx: boom"]
    assert payload["audit_log"] == "/data/audit.csv"


# ── --schedule foreground mode ───────────────────────────────────────────
def test_schedule_rejects_bad_time(monkeypatch):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)
    args = _headless_args(main, headless=False, schedule="9:99")
    assert main._run_schedule(args) == main.EXIT_CONFIG


def test_schedule_requires_tokens(monkeypatch):
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: False)
    args = _headless_args(main, headless=False, schedule="23:00")
    assert main._run_schedule(args) == main.EXIT_CONFIG


def test_schedule_starts_autoscheduler_and_stops_cleanly(monkeypatch, tmp_path):
    """--schedule wires AutoScheduler.start and returns EXIT_OK on stop.

    The real AutoScheduler is replaced with a fake so no Timer thread runs;
    the test asserts the wiring (time, watch path, callback) and that the
    foreground wait loop exits once the scheduler reports not-running.
    """
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)

    started: dict = {}

    class _FakeScheduler:
        def __init__(self):
            self._running = False

        @property
        def is_running(self):
            # Report running once, then flip so the wait loop exits promptly.
            was = self._running
            self._running = False
            return was

        def start(self, time_str, watch_paths, callback):
            started["time"] = time_str
            started["watch_paths"] = watch_paths
            started["callback"] = callback
            self._running = True

        def stop(self):
            self._running = False

    import core.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "AutoScheduler", _FakeScheduler)

    watch = tmp_path / "ledgers"
    watch.mkdir()
    args = _headless_args(main, headless=False, schedule="23:00",
                          input=str(watch))
    assert main._run_schedule(args) == main.EXIT_OK
    assert started["time"] == "23:00"
    assert started["watch_paths"] == [str(watch)]
    assert callable(started["callback"])


def test_schedule_callback_runs_headless_batch(monkeypatch, tmp_path):
    """The scheduler's fire callback drives the headless batch (no network)."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_tokens_present", lambda: True)

    captured = _stub_headless_engine(monkeypatch, main, success=2, fail=0)

    fired: dict = {}

    class _FakeScheduler:
        def __init__(self):
            self._running = False

        @property
        def is_running(self):
            was = self._running
            self._running = False
            return was

        def start(self, time_str, watch_paths, callback):
            self._running = True
            fired["callback"] = callback

        def stop(self):
            self._running = False

    import core.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "AutoScheduler", _FakeScheduler)

    args = _headless_args(main, headless=False, schedule="23:00",
                          start_date="2025-01-02")
    assert main._run_schedule(args) == main.EXIT_OK
    # Simulate the scheduler firing with discovered files.
    fired["callback"](["/ledgers/a.xlsx", "/ledgers/b.xlsx"])
    assert captured["files"] == ["/ledgers/a.xlsx", "/ledgers/b.xlsx"]
    assert captured["start_date"] == "2025-01-02"


# ── --headless main() dispatch returns the contract exit code ────────────
def test_main_headless_dispatch_exits_with_run_headless_code(monkeypatch):
    """main() exits with whatever _run_headless returns (the contract code)."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--headless"])
    monkeypatch.setattr(main, "_run_headless", lambda args: main.EXIT_PARTIAL)

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == main.EXIT_PARTIAL


def test_help_epilog_documents_exit_codes(monkeypatch, capsys):
    """--help surfaces the exit-code table so cron authors can rely on it."""
    main = _import_main_with_fake_tk(monkeypatch)
    monkeypatch.setattr(main, "_ensure_directories", lambda: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])

    pytest = importlib.import_module("pytest")
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "partial failure" in out
    assert "nothing to do" in out
