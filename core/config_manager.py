#!/usr/bin/env python3
"""
core/config_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Persistent Settings Manager
---------------------------------------------------------------------------
JSON-backed configuration file for user preferences (appearance, auto-update,
custom directories). Gracefully handles missing or corrupt config files.
"""

import json
import logging
import os
from typing import Any, Dict

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
    """Load, save, and manage persistent user settings."""

    def __init__(self, config_dir: str | None = None):
        if config_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            config_dir = os.path.join(project_root, "data")
        self._config_dir = config_dir
        self._filepath = os.path.join(config_dir, SETTINGS_FILENAME)

    def load(self) -> Dict[str, Any]:
        """Load settings from disk. Returns defaults on any error."""
        if not os.path.exists(self._filepath):
            return dict(DEFAULT_SETTINGS)
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults to fill any missing keys
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning(
                "Settings file corrupt or unreadable (%s). Using defaults.",
                e,
            )
            return dict(DEFAULT_SETTINGS)

    def save(self, settings: Dict[str, Any]) -> None:
        """Persist settings to disk."""
        os.makedirs(self._config_dir, exist_ok=True)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting value."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a single setting value and persist."""
        settings = self.load()
        settings[key] = value
        self.save(settings)
