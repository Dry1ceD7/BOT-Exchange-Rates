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
    # v3.3.0: Role and governance controls
    "usage_mode": "admin",  # admin | operator
    "operator_write_access": False,
    "require_approval_before_write": False,
    # v3.3.0: Validation + external data
    "holiday_overlay_path": "",
    "enable_fx_fallback": True,
    "fx_fallback_base_url": "https://api.frankfurter.app",
    # v3.3.0: Notifications and reporting
    "notification_enabled": False,
    "notification_webhook_url": "",
    "job_history_limit": 30,
    # v3.3.0: Security hygiene
    "token_rotation_days": 90,
    "token_last_rotated": "",
    "token_expiry_date": "",
    # v3.3.0: Deployment / enterprise policy
    "silent_update": False,
    "policy_locked": False,
}

SETTINGS_FILENAME = "settings.json"
PROFILE_FILENAME_PREFIX = "settings."
ACTIVE_PROFILE_FILENAME = "active_profile.txt"
POLICY_FILENAME = "policy.json"


class SettingsManager:
    """Load, save, and manage persistent user settings with in-memory cache."""

    def __init__(
        self,
        config_dir: Optional[str] = None,
        profile: Optional[str] = None,
    ):
        if config_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            config_dir = os.path.join(project_root, "data")
        self._config_dir = config_dir
        self._active_profile_path = os.path.join(config_dir, ACTIVE_PROFILE_FILENAME)
        self._profile = self._resolve_profile(profile)
        self._filepath = self._profile_to_filepath(self._profile)
        self._cache: Optional[Dict[str, Any]] = None
        self._policy_cache: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    def _resolve_profile(self, requested: Optional[str]) -> str:
        profile = requested or os.environ.get("BOT_PROFILE") or self._read_active_profile()
        profile = (profile or "default").strip().lower()
        # Keep profile names filesystem-safe and predictable.
        profile = "".join(ch for ch in profile if ch.isalnum() or ch in ("-", "_"))
        return profile or "default"

    def _profile_to_filepath(self, profile: str) -> str:
        if profile == "default":
            return os.path.join(self._config_dir, SETTINGS_FILENAME)
        return os.path.join(self._config_dir, f"{PROFILE_FILENAME_PREFIX}{profile}.json")

    def _read_active_profile(self) -> str:
        try:
            with open(self._active_profile_path, "r", encoding="utf-8") as f:
                return f.read().strip() or "default"
        except OSError:
            return "default"

    def _load_policy(self) -> Dict[str, Any]:
        if self._policy_cache is not None:
            return dict(self._policy_cache)
        env_path = os.environ.get("BOT_POLICY_PATH", "").strip()
        policy_path = env_path or os.path.join(self._config_dir, POLICY_FILENAME)
        if not os.path.exists(policy_path):
            self._policy_cache = {}
            return {}
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                if isinstance(payload.get("settings"), dict):
                    policy = dict(payload["settings"])
                else:
                    policy = dict(payload)
            else:
                policy = {}
            self._policy_cache = policy
            return dict(policy)
        except (OSError, TypeError, json.JSONDecodeError):
            self._policy_cache = {}
            return {}

    @property
    def profile(self) -> str:
        return self._profile

    def list_profiles(self) -> list[str]:
        profiles = {"default"}
        try:
            for name in os.listdir(self._config_dir):
                if name.startswith(PROFILE_FILENAME_PREFIX) and name.endswith(".json"):
                    p = name[len(PROFILE_FILENAME_PREFIX):-5].strip()
                    if p:
                        profiles.add(p)
            if os.path.exists(os.path.join(self._config_dir, SETTINGS_FILENAME)):
                profiles.add("default")
        except OSError:
            pass
        return sorted(profiles)

    def set_active_profile(self, profile: str) -> None:
        profile_norm = self._resolve_profile(profile)
        os.makedirs(self._config_dir, exist_ok=True)
        with open(self._active_profile_path, "w", encoding="utf-8") as f:
            f.write(profile_norm)
        with self._lock:
            self._profile = profile_norm
            self._filepath = self._profile_to_filepath(profile_norm)
            self._cache = None

    def load(self) -> Dict[str, Any]:
        """Load settings from disk (cached after first read). Returns defaults on any error."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        return self._load_from_disk()

    def _load_from_disk(self) -> Dict[str, Any]:
        """Read settings from disk and update cache."""
        if not os.path.exists(self._filepath):
            # Backward-compatible fallback: if profile file is missing for
            # default profile, but legacy settings.json exists, keep using it.
            with self._lock:
                merged_defaults = dict(DEFAULT_SETTINGS)
                merged_defaults.update(self._load_policy())
                self._cache = merged_defaults
            return dict(self._cache)
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults to fill any missing keys
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            merged.update(self._load_policy())
            with self._lock:
                self._cache = merged
            return dict(merged)
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning(
                "Settings file corrupt or unreadable (%s). Using defaults.",
                e,
            )
            with self._lock:
                merged_defaults = dict(DEFAULT_SETTINGS)
                merged_defaults.update(self._load_policy())
                self._cache = merged_defaults
            return dict(self._cache)

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
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        merged.update(self._load_policy())
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
