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
import os
import tempfile
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "appearance": "system",
    "auto_update": True,
    "output_directory": "",
    "api_timeout_seconds": 10,
    # v3.1.0: Rate type for formula injection (buying_transfer, selling,
    #          buying_sight, mid_rate)
    "rate_type": "buying_transfer",
    # v3.1.0: Anomaly guardian threshold (±percentage)
    "anomaly_threshold_pct": 5.0,
    # v3.1.0: Scheduled auto-processing
    "scheduler_enabled": False,
    "scheduler_time": "23:00",
    "scheduler_paths": [],
}

SETTINGS_FILENAME = "settings.json"


class SettingsManager:
    """Load, save, and manage persistent user settings with in-memory cache."""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            config_dir = os.path.join(project_root, "data")
        self._config_dir = config_dir
        self._filepath = os.path.join(config_dir, SETTINGS_FILENAME)
        self._cache: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        """Load settings from disk (cached after first read). Returns defaults on any error."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        return self._load_from_disk()

    def _load_from_disk(self) -> Dict[str, Any]:
        """Read settings from disk and update cache."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            if not os.path.exists(self._filepath):
                self._cache = dict(DEFAULT_SETTINGS)
                return dict(DEFAULT_SETTINGS)
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
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

    def reload(self) -> Dict[str, Any]:
        """Force re-read from disk, bypassing cache."""
        with self._lock:
            self._cache = None
        return self._load_from_disk()

    def save(self, settings: Dict[str, Any]) -> None:
        """Persist settings to disk and update cache."""
        os.makedirs(self._config_dir, exist_ok=True)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
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
        os.replace(tmp_path, self._filepath)
        with self._lock:
            self._cache = merged

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting value (from cache)."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a single setting value and persist."""
        settings = self.load()
        settings[key] = value
        self.save(settings)
