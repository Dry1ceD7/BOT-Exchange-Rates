#!/usr/bin/env python3
"""
core/config_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Persistent Settings Manager
---------------------------------------------------------------------------
JSON-backed configuration file for user preferences (appearance, auto-update,
custom directories). Gracefully handles missing or corrupt config files.

v3.2.2: In-memory caching to avoid re-reading disk on every get() call.
"""

import json
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    "appearance": "system",
    # v3.3.0: UI language ('en' default, 'th' Thai). Read by core/i18n.py.
    #          Only on-screen text is translated; logs/audit CSV stay English.
    "language": "en",
    "auto_update": True,
    "api_timeout_seconds": 10,
    # Rate type for ledger formula injection. USD/EUR (and extra currencies)
    # support buying_transfer ("Buying TT") and selling only — those are the
    # rates fetched/stored and the only ExRate columns that exist.
    "rate_type": "buying_transfer",
    # v3.1.0: Anomaly guardian threshold (±percentage)
    "anomaly_threshold_pct": 5.0,
    # v3.1.0: Scheduled auto-processing
    "scheduler_enabled": False,
    "scheduler_time": "23:00",
    "scheduler_paths": [],
}

SETTINGS_FILENAME = "settings.json"

# Keys that must NEVER be written to an exported settings file or accepted from
# an imported one. Secrets live in the OS keyring (core/secure_tokens.py), not
# here — but this denylist is a belt-and-suspenders guard so that if a token-ish
# key is ever added to the settings dict by mistake it can never leak through the
# multi-PC export/import path. Matching is case-insensitive substring on the key.
_SENSITIVE_KEY_MARKERS = ("token", "secret", "password", "passwd", "apikey", "api_key", "key")


class SettingsManager:
    """Load, save, and manage persistent user settings with in-memory cache."""

    def __init__(self, config_dir: str | None = None):
        if config_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            config_dir = str(Path(project_root) / "data")
        self._config_dir = config_dir
        # Keep _filepath as str: consumed by open()/json and os.replace below.
        self._filepath = str(Path(config_dir) / SETTINGS_FILENAME)
        self._cache: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        """Load settings from disk (cached after first read). Returns defaults on any error."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        return self._load_from_disk()

    def _load_from_disk(self, force: bool = False) -> dict[str, Any]:
        """Read settings from disk and update cache.

        Args:
            force: When True, bypasses cache and always reads from disk.
        """
        with self._lock:
            if not force and self._cache is not None:
                return dict(self._cache)
            if not Path(self._filepath).exists():
                self._cache = dict(DEFAULT_SETTINGS)
                return dict(DEFAULT_SETTINGS)
            try:
                with Path(self._filepath).open(encoding="utf-8") as f:
                    data = json.load(f)
                # Merge with defaults to fill any missing keys
                merged = dict(DEFAULT_SETTINGS)
                merged.update(data)
                self._cache = merged
                return dict(merged)
            except (json.JSONDecodeError, OSError, TypeError) as e:
                logger.warning(
                    "Settings file corrupt or unreadable (%s). Using defaults.",
                    e,
                )
                self._cache = dict(DEFAULT_SETTINGS)
                return dict(DEFAULT_SETTINGS)

    def reload(self) -> dict[str, Any]:
        """Force re-read from disk, bypassing cache."""
        with self._lock:
            self._cache = None
        return self._load_from_disk(force=True)

    def save(self, settings: dict[str, Any]) -> None:
        """Persist settings to disk and update cache."""
        with self._lock:
            self._save_locked(settings)

    def _save_locked(self, settings: dict[str, Any]) -> None:
        """Persist settings to disk and update cache. Caller holds the lock."""
        Path(self._config_dir).mkdir(parents=True, exist_ok=True)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._config_dir,
                prefix="settings.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(merged, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            # Path.replace is os.replace under the hood — atomic same-FS rename.
            Path(tmp_path).replace(self._filepath)
        except OSError:
            try:
                if tmp_path and Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            except OSError:
                pass
            raise
        finally:
            try:
                if tmp_path and Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            except OSError:
                pass
        self._cache = merged

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting value (from cache)."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a single setting value and persist.

        Holds the lock across the full load→modify→save cycle so two
        concurrent set() calls cannot clobber each other (the scheduler
        runs on a background thread, so concurrent sets are real).
        """
        with self._lock:
            if self._cache is not None:
                settings = dict(self._cache)
                settings[key] = value
                self._save_locked(settings)
                return
        # Cache was cold — populate it from disk (acquires the lock), then
        # retry the locked read-modify-write.
        self._load_from_disk()
        with self._lock:
            settings = dict(self._cache) if self._cache is not None \
                else dict(DEFAULT_SETTINGS)
            settings[key] = value
            self._save_locked(settings)

    # ------------------------------------------------------------------ #
    #  Multi-PC deployment: export / import the settings JSON
    # ------------------------------------------------------------------ #
    @staticmethod
    def _strip_sensitive(settings: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``settings`` with any sensitive-looking key removed.

        Secrets are stored in the OS keyring, not in settings.json, so this is a
        defensive guard: an admin can copy an exported file to another PC without
        any chance of leaking a token even if one were ever (mistakenly) parked
        in the settings dict.
        """
        return {
            k: v
            for k, v in settings.items()
            if not any(m in k.lower() for m in _SENSITIVE_KEY_MARKERS)
        }

    def export_settings(self, dest_path: str) -> str:
        """Write the current settings (minus secrets) to ``dest_path`` as JSON.

        The exported file is a portable, human-readable snapshot intended for
        copying to another PC. Returns the destination path on success. Raises
        OSError on a write failure (callers humanize it for the user).
        """
        # load() returns a defaults-merged copy, so every known key is present
        # and the file is self-describing on the target machine.
        exportable = self._strip_sensitive(self.load())
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as f:
            json.dump(exportable, f, indent=2, ensure_ascii=False)
        return str(dest)

    def import_settings(self, src_path: str) -> dict[str, Any]:
        """Load settings from ``src_path``, merge them in, persist, and return.

        Only keys recognised in DEFAULT_SETTINGS are accepted (unknown keys are
        dropped so a hand-edited or foreign file can't inject junk), and any
        sensitive-looking key is stripped. The merged result is persisted via the
        normal locked save path and the in-memory cache is refreshed.

        Raises ValueError if the file is not valid JSON or not a JSON object, and
        OSError if it cannot be read (callers humanize these for the user).
        """
        with Path(src_path).open(encoding="utf-8") as f:
            try:
                incoming = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Not a valid settings file: {e}") from e
        if not isinstance(incoming, dict):
            raise ValueError("Settings file must contain a JSON object.")
        # Accept only known keys, then strip any sensitive ones.
        accepted = self._strip_sensitive(
            {k: v for k, v in incoming.items() if k in DEFAULT_SETTINGS}
        )
        with self._lock:
            base = dict(self._cache) if self._cache is not None \
                else dict(DEFAULT_SETTINGS)
            base.update(accepted)
            self._save_locked(base)
            return dict(self._cache) if self._cache is not None else base
