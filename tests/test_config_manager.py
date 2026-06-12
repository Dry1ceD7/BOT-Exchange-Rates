#!/usr/bin/env python3
"""
tests/test_config_manager.py
---------------------------------------------------------------------------
Unit tests for core/config_manager.py — Settings persistence and caching.
---------------------------------------------------------------------------
"""

import json
import logging
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

    @pytest.mark.parametrize("content", ['"th"', "42", '["ab"]'])
    def test_load_non_object_json_returns_defaults(self, config_dir, content):
        """Round 11: settings.json holding a JSON scalar or array (valid
        JSON, but not an object) must return pure defaults — '"th"' used to
        raise an uncaught ValueError from dict.update on every load, and a
        pair-like array (["ab"]) silently merged as {"a": "b"} key junk."""
        filepath = os.path.join(config_dir, "settings.json")
        os.makedirs(config_dir, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)

        mgr = SettingsManager(config_dir=config_dir)
        settings = mgr.load()  # must not raise
        assert settings == DEFAULT_SETTINGS
        assert mgr.get("language") == DEFAULT_SETTINGS["language"]

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


class TestDefaultSettingsKeys:
    """Guard the DEFAULT_SETTINGS key set against dead/regressed entries."""

    def test_output_directory_removed(self):
        """'output_directory' had zero readers and must stay removed."""
        assert "output_directory" not in DEFAULT_SETTINGS

    def test_api_timeout_seconds_retained(self):
        """'api_timeout_seconds' is wired into the client and must remain."""
        assert "api_timeout_seconds" in DEFAULT_SETTINGS

    def test_api_timeout_default_is_the_constant(self):
        """F29: the default mirrors core.constants.API_TIMEOUT_SECONDS."""
        from core.constants import API_TIMEOUT_SECONDS

        assert DEFAULT_SETTINGS["api_timeout_seconds"] == API_TIMEOUT_SECONDS

    def test_scheduler_skip_keys_default_off(self):
        """F28: skip-weekends/holidays exist and default to False so
        existing installs keep firing every day unchanged."""
        assert DEFAULT_SETTINGS["scheduler_skip_weekends"] is False
        assert DEFAULT_SETTINGS["scheduler_skip_holidays"] is False

    def test_scheduler_last_run_not_seeded(self):
        """'scheduler_last_run' is machine-local run state — never a default
        (it must not be seeded, exported, or imported across PCs)."""
        assert "scheduler_last_run" not in DEFAULT_SETTINGS


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


class TestSettingsManagerExport:
    """Tests for export_settings() — multi-PC deployment snapshots."""

    def test_export_writes_json_file(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        mgr.set("appearance", "dark")
        dest = str(tmp_path / "out" / "exported.json")
        returned = mgr.export_settings(dest)
        assert returned == dest
        assert os.path.exists(dest)
        with open(dest, encoding="utf-8") as f:
            data = json.load(f)
        assert data["appearance"] == "dark"

    def test_export_includes_all_default_keys(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        dest = str(tmp_path / "exported.json")
        mgr.export_settings(dest)
        with open(dest, encoding="utf-8") as f:
            data = json.load(f)
        # Every known, non-sensitive setting should round-trip.
        assert data["rate_type"] == DEFAULT_SETTINGS["rate_type"]
        assert data["anomaly_threshold_pct"] == DEFAULT_SETTINGS[
            "anomaly_threshold_pct"
        ]
        assert "language" in data

    def test_export_strips_sensitive_keys(self, config_dir, tmp_path):
        """A token-ish key parked in settings must NEVER be exported."""
        mgr = SettingsManager(config_dir=config_dir)
        # Sneak a sensitive-looking key into the persisted settings.
        mgr.set("bot_api_token", "super-secret-value")
        mgr.set("user_password", "hunter2")
        dest = str(tmp_path / "exported.json")
        mgr.export_settings(dest)
        with open(dest, encoding="utf-8") as f:
            data = json.load(f)
        assert "bot_api_token" not in data
        assert "user_password" not in data
        # Non-sensitive keys still present.
        assert "appearance" in data

    def test_export_raises_oserror_on_bad_dir(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        # A path whose parent cannot be created (a file used as a dir).
        bad = os.path.join(config_dir, "afile")
        with open(bad, "w") as f:
            f.write("x")
        with pytest.raises(OSError):
            mgr.export_settings(os.path.join(bad, "nested", "out.json"))


class TestSettingsManagerImport:
    """Tests for import_settings() — accept known keys, drop junk/secrets."""

    def _write_json(self, path, payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_import_applies_known_keys(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"appearance": "light", "rate_type": "selling"})
        result = mgr.import_settings(src)
        assert result["appearance"] == "light"
        assert result["rate_type"] == "selling"
        # Persisted: a fresh manager sees the imported values.
        mgr2 = SettingsManager(config_dir=config_dir)
        assert mgr2.get("appearance") == "light"
        assert mgr2.get("rate_type") == "selling"

    def test_import_drops_unknown_keys(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"appearance": "dark", "junk_key": "nope"})
        result = mgr.import_settings(src)
        assert result["appearance"] == "dark"
        assert "junk_key" not in result

    def test_import_strips_sensitive_keys(self, config_dir, tmp_path):
        """Even a known-looking secret key must not be imported."""
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        # 'api_timeout_seconds' is known and safe; a token key must be dropped.
        self._write_json(
            src,
            {
                "appearance": "dark",
                "api_timeout_seconds": 30,
                "secret_token": "leak-me",
            },
        )
        result = mgr.import_settings(src)
        assert result["appearance"] == "dark"
        assert result["api_timeout_seconds"] == 30
        assert "secret_token" not in result

    def test_import_preserves_unspecified_keys(self, config_dir, tmp_path):
        """Keys not in the imported file keep their current value."""
        mgr = SettingsManager(config_dir=config_dir)
        mgr.set("anomaly_threshold_pct", 9.0)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"appearance": "light"})
        result = mgr.import_settings(src)
        assert result["appearance"] == "light"
        assert result["anomaly_threshold_pct"] == 9.0

    def test_import_raises_valueerror_on_bad_json(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "bad.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with pytest.raises(ValueError):
            mgr.import_settings(src)

    def test_import_raises_valueerror_on_non_object(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "list.json")
        self._write_json(src, ["not", "an", "object"])
        with pytest.raises(ValueError):
            mgr.import_settings(src)

    def test_import_raises_oserror_on_missing_file(self, config_dir):
        mgr = SettingsManager(config_dir=config_dir)
        with pytest.raises(OSError):
            mgr.import_settings(os.path.join(config_dir, "does_not_exist.json"))

    def test_export_then_import_roundtrip(self, config_dir, tmp_path):
        """A settings snapshot survives an export -> import cycle."""
        src_mgr = SettingsManager(config_dir=config_dir)
        src_mgr.set("appearance", "dark")
        src_mgr.set("rate_type", "mid_rate")
        src_mgr.set("anomaly_threshold_pct", 7.25)
        snapshot = str(tmp_path / "snap.json")
        src_mgr.export_settings(snapshot)

        # Import into a different "PC" (separate config dir).
        other_dir = str(tmp_path / "other_pc")
        dst_mgr = SettingsManager(config_dir=other_dir)
        result = dst_mgr.import_settings(snapshot)
        assert result["appearance"] == "dark"
        assert result["rate_type"] == "mid_rate"
        assert result["anomaly_threshold_pct"] == 7.25


class TestMultiInstanceConsistency:
    """F3 regression: per-instance caches must not cause lost updates.

    Seven-plus long-lived SettingsManager instances coexist in the app (GUI
    app, scheduler panel, settings modal, engine, API client, i18n, update
    banner). set()/import_settings() must merge over the latest ON-DISK
    state, never a stale instance cache, or instance B's save silently
    reverts a key instance A just persisted.
    """

    def test_set_on_second_instance_preserves_first_instances_key(
        self, config_dir
    ):
        a = SettingsManager(config_dir=config_dir)
        b = SettingsManager(config_dir=config_dir)
        # Warm BOTH caches with defaults so B's cache goes stale after A.set.
        a.load()
        b.load()
        a.set("appearance", "dark")
        b.set("language", "th")
        # A fresh reader sees BOTH writes — B did not clobber A's key.
        fresh = SettingsManager(config_dir=config_dir).load()
        assert fresh["appearance"] == "dark"
        assert fresh["language"] == "th"

    def test_set_refreshes_own_cache_with_sibling_writes(self, config_dir):
        a = SettingsManager(config_dir=config_dir)
        b = SettingsManager(config_dir=config_dir)
        a.load()
        b.load()
        a.set("appearance", "dark")
        b.set("language", "th")
        # B's post-save cache reflects A's earlier write too.
        assert b.get("appearance") == "dark"
        assert b.get("language") == "th"

    def test_import_on_second_instance_preserves_first_instances_key(
        self, config_dir, tmp_path
    ):
        a = SettingsManager(config_dir=config_dir)
        b = SettingsManager(config_dir=config_dir)
        a.load()
        b.load()
        a.set("anomaly_threshold_pct", 9.0)
        src = str(tmp_path / "in.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump({"appearance": "light"}, f)
        result = b.import_settings(src)
        assert result["appearance"] == "light"
        assert result["anomaly_threshold_pct"] == 9.0


class TestImportTypeCoercion:
    """F27 regression: imported values are coerced to DEFAULT_SETTINGS types.

    A hand-edited file carrying e.g. a string anomaly_threshold_pct must not
    propagate a str into the anomaly guard (uncaught TypeError aborts the
    batch). Garbage falls back to the default with a warning naming the key.
    """

    def _write_json(self, path, payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_numeric_string_threshold_coerces_to_float(
        self, config_dir, tmp_path
    ):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"anomaly_threshold_pct": "7.5"})
        result = mgr.import_settings(src)
        assert result["anomaly_threshold_pct"] == 7.5
        assert isinstance(result["anomaly_threshold_pct"], float)

    def test_numeric_string_timeout_coerces_to_float(self, config_dir, tmp_path):
        # F29: the default is core.constants.API_TIMEOUT_SECONDS (a float),
        # so imported strings coerce to float, matching the default's type.
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"api_timeout_seconds": "30"})
        result = mgr.import_settings(src)
        assert result["api_timeout_seconds"] == 30
        assert isinstance(result["api_timeout_seconds"], float)

    def test_garbage_threshold_falls_back_to_default_with_warning(
        self, config_dir, tmp_path, caplog
    ):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"anomaly_threshold_pct": "not-a-number"})
        with caplog.at_level(logging.WARNING, logger="core.config_manager"):
            result = mgr.import_settings(src)
        assert result["anomaly_threshold_pct"] == DEFAULT_SETTINGS[
            "anomaly_threshold_pct"
        ]
        assert any(
            "anomaly_threshold_pct" in rec.getMessage()
            for rec in caplog.records
        )

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", -3, 0, 1e9, None])
    def test_nonfinite_or_out_of_range_threshold_falls_back(
        self, config_dir, tmp_path, bad
    ):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"anomaly_threshold_pct": bad})
        result = mgr.import_settings(src)
        assert result["anomaly_threshold_pct"] == DEFAULT_SETTINGS[
            "anomaly_threshold_pct"
        ]

    @pytest.mark.parametrize("bad", [0.001, 1e12])
    def test_out_of_range_api_timeout_falls_back(
        self, config_dir, tmp_path, bad, caplog
    ):
        """Round 11: an imported api_timeout_seconds outside the
        [MIN_API_TIMEOUT_SECONDS, MAX_API_TIMEOUT_SECONDS] bounds falls back
        to the default — 0.001 would make every BOT call time out (with
        tenacity retrying each chunk 4 times)."""
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"api_timeout_seconds": bad})
        with caplog.at_level(logging.WARNING, logger="core.config_manager"):
            result = mgr.import_settings(src)
        assert result["api_timeout_seconds"] == DEFAULT_SETTINGS[
            "api_timeout_seconds"
        ]
        assert any(
            "api_timeout_seconds" in rec.getMessage()
            for rec in caplog.records
        )

    def test_in_range_api_timeout_is_honored(self, config_dir, tmp_path):
        """An in-range imported timeout (e.g. 7) must still be accepted."""
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"api_timeout_seconds": 7})
        result = mgr.import_settings(src)
        assert result["api_timeout_seconds"] == 7.0

    def test_boolean_string_coerces(self, config_dir, tmp_path):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(
            src, {"auto_update": "false", "scheduler_enabled": "true"}
        )
        result = mgr.import_settings(src)
        assert result["auto_update"] is False
        assert result["scheduler_enabled"] is True

    def test_garbage_boolean_falls_back_to_default_with_warning(
        self, config_dir, tmp_path, caplog
    ):
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"auto_update": "maybe"})
        with caplog.at_level(logging.WARNING, logger="core.config_manager"):
            result = mgr.import_settings(src)
        assert result["auto_update"] is DEFAULT_SETTINGS["auto_update"]
        assert any(
            "auto_update" in rec.getMessage() for rec in caplog.records
        )

    def test_wrong_typed_string_key_falls_back(self, config_dir, tmp_path):
        """A non-string rate_type (e.g. a number) falls back to the default."""
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"rate_type": 123, "scheduler_paths": "notalist"})
        result = mgr.import_settings(src)
        assert result["rate_type"] == DEFAULT_SETTINGS["rate_type"]
        assert result["scheduler_paths"] == DEFAULT_SETTINGS["scheduler_paths"]

    def test_coerced_import_persists_to_disk(self, config_dir, tmp_path):
        """The coerced (not raw) value is what lands in settings.json."""
        mgr = SettingsManager(config_dir=config_dir)
        src = str(tmp_path / "in.json")
        self._write_json(src, {"anomaly_threshold_pct": "8.25"})
        mgr.import_settings(src)
        fresh = SettingsManager(config_dir=config_dir)
        assert fresh.get("anomaly_threshold_pct") == 8.25
