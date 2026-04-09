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

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "bot_exrate"

# Token keys used in .env files
_ENV_TOKEN_KEYS = {
    "BOT_TOKEN_EXG": "bot_token_exg",
    "BOT_TOKEN_HOL": "bot_token_hol",
}


def _keyring_available() -> bool:
    """Check if keyring backend is functional."""
    try:
        import keyring
        # Test that a real backend is available (not the fail backend)
        backend = keyring.get_keyring()
        backend_name = type(backend).__name__
        if "Fail" in backend_name or "Null" in backend_name:
            return False
        return True
    except Exception:
        return False


def get_token(env_key: str) -> Optional[str]:
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


def migrate_env_to_keychain(env_path: Optional[str] = None) -> int:
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
