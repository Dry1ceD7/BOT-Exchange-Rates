#!/usr/bin/env python3
"""Tests for core/secure_tokens.py — Keychain-first token management."""

from unittest.mock import MagicMock, patch

import pytest

from core.secure_tokens import (
    SERVICE_NAME,
    _keyring_available,
    _purge_env_file_token,
    delete_token,
    get_token,
    set_token,
)

# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no real BOT tokens leak into tests from the environment."""
    monkeypatch.delenv("BOT_TOKEN_EXG", raising=False)
    monkeypatch.delenv("BOT_TOKEN_HOL", raising=False)


# Helper to create a mock keyring module that functions correctly
def _make_keyring_mock(get_password_rv=None, backend_name="KeychainBackend"):
    """Return a mock keyring module with configurable responses."""
    mock_kr = MagicMock()
    mock_backend = MagicMock()
    type(mock_backend).__name__ = backend_name
    mock_kr.get_keyring.return_value = mock_backend
    mock_kr.get_password.return_value = get_password_rv
    return mock_kr


# ── _keyring_available ──────────────────────────────────────────────────

class TestKeyringAvailable:
    """Tests for the keyring availability probe."""

    def test_returns_true_for_real_backend(self):
        mock_kr = _make_keyring_mock(backend_name="KeychainBackend")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            assert _keyring_available() is True

    def test_returns_false_for_fail_backend(self):
        mock_kr = _make_keyring_mock(backend_name="FailKeyring")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            assert _keyring_available() is False

    def test_returns_false_when_import_fails(self):
        with patch.dict("sys.modules", {"keyring": None}):
            assert _keyring_available() is False


# ── get_token ───────────────────────────────────────────────────────────

class TestGetToken:
    """Tests for keychain-first token retrieval."""

    def test_returns_keychain_token(self):
        mock_kr = _make_keyring_mock(get_password_rv="keychain_secret")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = get_token("BOT_TOKEN_EXG")
        assert result == "keychain_secret"
        mock_kr.get_password.assert_called_once_with(
            SERVICE_NAME, "bot_token_exg"
        )

    @patch("core.secure_tokens._keyring_available", return_value=False)
    def test_falls_back_to_env(self, mock_avail, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN_EXG", "env_secret")
        result = get_token("BOT_TOKEN_EXG")
        assert result == "env_secret"

    @patch("core.secure_tokens._keyring_available", return_value=False)
    def test_returns_none_when_not_found(self, mock_avail):
        result = get_token("BOT_TOKEN_EXG")
        assert result is None

    def test_auto_migrates_env_to_keychain(self, monkeypatch):
        """When token is in env but not keychain, it should auto-migrate."""
        mock_kr = _make_keyring_mock(get_password_rv=None)
        monkeypatch.setenv("BOT_TOKEN_EXG", "migrate_me")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = get_token("BOT_TOKEN_EXG")
        assert result == "migrate_me"
        mock_kr.set_password.assert_called_once_with(
            SERVICE_NAME, "bot_token_exg", "migrate_me"
        )

    def test_auto_migration_purges_env_file(self, tmp_path, monkeypatch):
        """SECURITY: a successful keychain migration triggers a .env purge.

        get_token resolves the .env path internally via get_project_root, so
        we assert that the purge helper is invoked for the migrated key
        (the file-rewrite behavior itself is covered by TestPurgeEnvFileToken).
        """
        # Keychain is available, but has no token yet → forces the
        # env→keychain migration path (which performs the .env purge).
        mock_kr = _make_keyring_mock(get_password_rv=None)
        monkeypatch.setattr(
            "core.secure_tokens._keyring_available", lambda: True
        )
        monkeypatch.setenv("BOT_TOKEN_EXG", "migrate_me")
        purge_calls = []
        monkeypatch.setattr(
            "core.secure_tokens._purge_env_file_token",
            lambda key, env_path=None: purge_calls.append(key),
        )
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = get_token("BOT_TOKEN_EXG")
        assert result == "migrate_me"
        assert "BOT_TOKEN_EXG" in purge_calls


# ── _purge_env_file_token ───────────────────────────────────────────────

class TestPurgeEnvFileToken:
    """Tests for the .env plaintext purge helper."""

    def test_removes_only_matching_line(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "BOT_TOKEN_EXG=secret\nBOT_TOKEN_HOL=other\nFOO=bar\n",
            encoding="utf-8",
        )
        _purge_env_file_token("BOT_TOKEN_EXG", env_path=str(env_path))
        content = env_path.read_text(encoding="utf-8")
        assert "BOT_TOKEN_EXG" not in content
        assert "BOT_TOKEN_HOL=other" in content
        assert "FOO=bar" in content

    def test_noop_when_missing_file(self, tmp_path):
        # Should not raise.
        _purge_env_file_token(
            "BOT_TOKEN_EXG", env_path=str(tmp_path / "nope.env")
        )

    def test_keychain_read_failure_falls_back(self, monkeypatch):
        """When keychain read fails, should fall back to env."""
        mock_kr = _make_keyring_mock()
        mock_kr.get_password.side_effect = Exception("Backend error")
        monkeypatch.setenv("BOT_TOKEN_EXG", "fallback_secret")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = get_token("BOT_TOKEN_EXG")
        assert result == "fallback_secret"


# ── set_token ───────────────────────────────────────────────────────────

class TestSetToken:
    """Tests for keychain token storage."""

    def test_stores_token_successfully(self):
        mock_kr = _make_keyring_mock()
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = set_token("BOT_TOKEN_EXG", "new_secret")
        assert result is True
        mock_kr.set_password.assert_called_once_with(
            SERVICE_NAME, "bot_token_exg", "new_secret"
        )

    @patch("core.secure_tokens._keyring_available", return_value=False)
    def test_returns_false_without_keyring(self, mock_avail):
        result = set_token("BOT_TOKEN_EXG", "new_secret")
        assert result is False

    def test_returns_false_on_error(self):
        mock_kr = _make_keyring_mock()
        mock_kr.set_password.side_effect = Exception("Write failure")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = set_token("BOT_TOKEN_EXG", "new_secret")
        assert result is False


# ── delete_token ────────────────────────────────────────────────────────

class TestDeleteToken:
    """Tests for keychain token deletion."""

    def test_deletes_token(self):
        mock_kr = _make_keyring_mock()
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = delete_token("BOT_TOKEN_EXG")
        assert result is True
        mock_kr.delete_password.assert_called_once()

    @patch("core.secure_tokens._keyring_available", return_value=False)
    def test_noop_without_keyring(self, mock_avail):
        result = delete_token("BOT_TOKEN_EXG")
        assert result is False
