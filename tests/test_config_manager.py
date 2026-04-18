#!/usr/bin/env python3
"""
tests/test_config_manager.py
---------------------------------------------------------------------------
Unit tests for core/config_manager.py — Settings persistence and caching.
---------------------------------------------------------------------------
"""

import json
import os

import pytest

from core.config_manager import DEFAULT_SETTINGS, SettingsManager


@pytest.fixture
def config_dir(tmp_path):
    """Provide a temporary config directory."""
    return str(tmp_path)


class TestSettingsManagerLoad:
    """Tests for loading settings."""

    def test_load_returns_defaults_when_no_file(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        settings = mgr.load()
        assert settings == DEFAULT_SETTINGS

    def test_load_reads_saved_file(self, config_dir):
        filepath = os.path.join(config_dir, "settings.json")
        custom = {"appearance": "dark", "auto_update": False}
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(custom, f)

        mgr = SettingsManager(config_dir=config_dir)
        settings = mgr.load()
        assert settings["appearance"] == "dark"
        assert settings["auto_update"] is False

    def test_load_merges_missing_keys_with_defaults(self, config_dir):
        """Saved file with partial keys should be merged with defaults."""
        filepath = os.path.join(config_dir, "settings.json")
        partial = {"appearance": "light"}
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(partial, f)

        mgr = SettingsManager(config_dir=config_dir)
        settings = mgr.load()
        assert settings["appearance"] == "light"
        # Missing keys should have default values
        assert settings["auto_update"] == DEFAULT_SETTINGS["auto_update"]
        assert settings["rate_type"] == DEFAULT_SETTINGS["rate_type"]

    def test_load_handles_corrupt_json(self, config_dir):
        """Corrupt JSON returns defaults without crashing."""
        filepath = os.path.join(config_dir, "settings.json")
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            f.write("{invalid json content!!!")

        mgr = SettingsManager(config_dir=config_dir)
        settings = mgr.load()
        assert settings == DEFAULT_SETTINGS

    def test_load_caches_result(self, config_dir):
        """Second load() returns cached copy without re-reading disk."""
        mgr = SettingsManager(config_dir=config_dir)
        first = mgr.load()
        # Write a different file to disk
        filepath = os.path.join(config_dir, "settings.json")
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump({"appearance": "dark"}, f)
        # Should still get cached defaults
        second = mgr.load()
        assert second["appearance"] == first["appearance"]


class TestSettingsManagerSave:
    """Tests for saving settings."""

    def test_save_creates_file(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.save({"appearance": "dark"})
        filepath = os.path.join(config_dir, "settings.json")
        assert os.path.exists(filepath)
        with open(filepath) as f:
            data = json.load(f)
        assert data["appearance"] == "dark"

    def test_save_merges_with_defaults(self, config_dir):
        """Saving partial settings should merge with defaults."""
        mgr = SettingsManager(config_dir=config_dir)
        mgr.save({"appearance": "dark"})
        filepath = os.path.join(config_dir, "settings.json")
        with open(filepath) as f:
            data = json.load(f)
        assert data["auto_update"] == DEFAULT_SETTINGS["auto_update"]

    def test_save_updates_cache(self, config_dir):
        """After save(), load() returns the new values from cache."""
        mgr = SettingsManager(config_dir=config_dir)
        mgr.save({"appearance": "dark"})
        settings = mgr.load()
        assert settings["appearance"] == "dark"


class TestSettingsManagerGetSet:
    """Tests for get() and set() convenience methods."""

    def test_get_returns_default_value(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        assert mgr.get("appearance") == "system"

    def test_get_returns_fallback_for_unknown_key(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        assert mgr.get("nonexistent_key", "fallback") == "fallback"

    def test_set_persists_value(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.set("appearance", "dark")
        assert mgr.get("appearance") == "dark"

    def test_set_saves_to_disk(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.set("auto_update", False)
        # Verify via fresh manager
        mgr2 = SettingsManager(config_dir=config_dir)
        assert mgr2.get("auto_update") is False


class TestSettingsManagerReload:
    """Tests for reload() cache invalidation."""

    def test_reload_reads_fresh_from_disk(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.load()  # Populate cache with defaults

        # Write new data directly to disk
        filepath = os.path.join(config_dir, "settings.json")
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump({"appearance": "dark"}, f)

        # reload() should bypass cache
        settings = mgr.reload()
        assert settings["appearance"] == "dark"


class TestSettingsManagerProfilesAndPolicy:
    """Tests for profile routing and policy override behavior."""

    def test_set_active_profile_changes_target_file(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.set_active_profile("finance")
        mgr.save({"appearance": "dark"})
        prof_path = os.path.join(config_dir, "settings.finance.json")
        assert os.path.exists(prof_path)

        # Fresh manager should resolve profile from active_profile.txt
        mgr2 = SettingsManager(config_dir=config_dir)
        assert mgr2.profile == "finance"
        assert mgr2.get("appearance") == "dark"

    def test_policy_overrides_loaded_values(self, config_dir, monkeypatch):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.save({"auto_update": True, "usage_mode": "admin"})
        policy_path = os.path.join(config_dir, "policy.json")
        with open(policy_path, "w", encoding="utf-8") as f:
            json.dump({"auto_update": False, "usage_mode": "operator"}, f)
        monkeypatch.setenv("BOT_POLICY_PATH", policy_path)

        mgr2 = SettingsManager(config_dir=config_dir)
        settings = mgr2.load()
        assert settings["auto_update"] is False
        assert settings["usage_mode"] == "operator"
