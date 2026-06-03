#!/usr/bin/env python3
"""
core/secure_tokens.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Secure Token Manager
---------------------------------------------------------------------------
Manages API tokens via the OS keychain (macOS Keychain, Windows Credential
Manager, Linux SecretService) with automatic migration from plaintext .env.

v3.2.2: Replaces plaintext .env token storage with OS-native secure storage.
"""

import contextlib
import logging
import os

logger = logging.getLogger(__name__)

SERVICE_NAME = "bot_exrate"

# Token keys used in .env files
_ENV_TOKEN_KEYS = {
    "BOT_TOKEN_EXG": "bot_token_exg",
    "BOT_TOKEN_HOL": "bot_token_hol",
}


def _purge_env_file_token(env_key: str, env_path: str | None = None) -> None:
    """Strip a BOT_TOKEN_* line from the on-disk .env file.

    Called after a token has been migrated into the OS keychain so the
    plaintext copy no longer lingers in .env. Best-effort: silently
    no-ops if the file is missing or unreadable. Also chmods the file
    to 0o600 if any token lines remain.
    """
    if env_path is None:
        try:
            from core.paths import get_project_root
            env_path = os.path.join(get_project_root(), ".env")
        except Exception:
            return
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        kept = [
            ln for ln in lines
            if not ln.strip().startswith(f"{env_key}=")
        ]
        if kept != lines:
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(kept)
            logger.debug("Purged '%s' from .env after keychain migration.", env_key)
        # Lock down the .env in case any other secrets remain
        with contextlib.suppress(OSError):
            os.chmod(env_path, 0o600)
    except OSError as e:
        logger.debug("Could not purge '%s' from .env: %s", env_key, e)


def _keyring_available() -> bool:
    """Check if keyring backend is functional."""
    try:
        import keyring
        # Test that a real backend is available (not the fail backend)
        backend = keyring.get_keyring()
        backend_name = type(backend).__name__
        return not ("Fail" in backend_name or "Null" in backend_name)
    except Exception:
        return False


def get_token(env_key: str) -> str | None:
    """
    Retrieve an API token, preferring keychain over .env.

    Priority:
    1. OS keychain (secure)
    2. os.environ / .env fallback (legacy)

    If found in .env but not keychain, auto-migrates to keychain.

    Args:
        env_key: The environment variable name (e.g., "BOT_TOKEN_EXG")

    Returns:
        The token string, or None if not found anywhere.
    """
    keyring_key = _ENV_TOKEN_KEYS.get(env_key, env_key.lower())

    # 1. Try keychain first
    if _keyring_available():
        try:
            import keyring
            token = keyring.get_password(SERVICE_NAME, keyring_key)
            if token:
                return token
        except Exception as e:
            logger.debug("Keyring read failed for %s: %s", keyring_key, e)

    # 2. Fallback to os.environ (.env loaded by dotenv)
    token = os.environ.get(env_key)
    if token:
        # Auto-migrate to keychain if available
        if _keyring_available():
            try:
                import keyring
                keyring.set_password(SERVICE_NAME, keyring_key, token)
                logger.info(
                    "Token '%s' migrated to OS keychain. "
                    "You can now remove it from .env.",
                    env_key,
                )
                # Scrub plaintext token from process environment to prevent
                # leakage via child processes or library introspection
                os.environ.pop(env_key, None)
                logger.debug("Scrubbed '%s' from os.environ after keychain migration.", env_key)
                # Also strip the plaintext copy from the on-disk .env so the
                # secret does not survive the keychain migration on disk.
                _purge_env_file_token(env_key)
            except Exception as e:
                logger.warning("Keyring migration failed for %s: %s", env_key, e)
        return token

    return None


def set_token(env_key: str, value: str) -> bool:
    """
    Store a token in the OS keychain.

    Args:
        env_key: The environment variable name (e.g., "BOT_TOKEN_EXG")
        value: The token string to store

    Returns:
        True if stored successfully, False otherwise.
    """
    keyring_key = _ENV_TOKEN_KEYS.get(env_key, env_key.lower())
    if not _keyring_available():
        logger.warning("No secure keyring backend available. Token not stored.")
        return False
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, keyring_key, value)
        logger.info("Token '%s' stored in OS keychain.", env_key)
        return True
    except Exception as e:
        logger.error("Failed to store token '%s': %s", env_key, e)
        return False


def delete_token(env_key: str) -> bool:
    """Remove a token from the OS keychain."""
    keyring_key = _ENV_TOKEN_KEYS.get(env_key, env_key.lower())
    if not _keyring_available():
        return False
    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, keyring_key)
        logger.info("Token '%s' removed from OS keychain.", env_key)
        return True
    except Exception as e:
        logger.debug("Failed to delete token '%s': %s", env_key, e)
        return False


def migrate_env_to_keychain(env_path: str | None = None) -> int:
    """
    One-time migration: read all tokens from .env and store in keychain.

    Returns the number of tokens successfully migrated.
    """
    if not _keyring_available():
        logger.info("Keyring not available — skipping migration.")
        return 0

    migrated = 0
    for env_key in _ENV_TOKEN_KEYS:
        token = os.environ.get(env_key)
        if token and set_token(env_key, token):
            migrated += 1

    if migrated > 0:
        logger.info(
            "Migrated %d token(s) to OS keychain. "
            "You can safely remove them from .env.",
            migrated,
        )
    return migrated
