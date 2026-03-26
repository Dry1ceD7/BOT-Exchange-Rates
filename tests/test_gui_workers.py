#!/usr/bin/env python3
"""
tests/test_gui_workers.py
---------------------------------------------------------------------------
TDGD RED PHASE: Failing Tests for GUI Workers & Modular Panels
---------------------------------------------------------------------------
These tests define the contracts for:
  1. core/workers/event_bus.py — Thread-safe Producer-Consumer event queue
  2. gui/panels/live_console.py — Read-only log viewer module
  3. gui/panels/settings_modal.py — JSON-backed settings persistence
  4. gui/panels/control_panel.py — Drop zone and action buttons module
"""

import os
import tempfile


# ═══════════════════════════════════════════════════════════════════════════
# 1. EVENT BUS (Producer-Consumer Queue)
# ═══════════════════════════════════════════════════════════════════════════
class TestEventBus:
    """Contract tests for the thread-safe event bus."""

    def test_event_bus_module_exists(self):
        from core.workers.event_bus import EventBus  # noqa: F401

    def test_can_push_and_drain(self):
        from core.workers.event_bus import EventBus

        bus = EventBus()
        bus.push({"type": "log", "msg": "Fetching USD..."})
        bus.push({"type": "progress", "value": 0.5})
        events = bus.drain()
        assert len(events) == 2
        assert events[0]["type"] == "log"
        assert events[1]["type"] == "progress"

    def test_drain_clears_queue(self):
        from core.workers.event_bus import EventBus

        bus = EventBus()
        bus.push({"type": "log", "msg": "test"})
        bus.drain()
        assert bus.drain() == []

    def test_push_is_thread_safe(self):
        """Pushing from multiple threads must not corrupt the queue."""
        import threading

        from core.workers.event_bus import EventBus

        bus = EventBus()
        def pusher(n):
            for i in range(100):
                bus.push({"id": n, "i": i})

        threads = [threading.Thread(target=pusher, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = bus.drain()
        assert len(events) == 400


# ═══════════════════════════════════════════════════════════════════════════
# 2. SETTINGS PERSISTENCE (JSON-backed)
# ═══════════════════════════════════════════════════════════════════════════
class TestSettingsManager:
    """Contract tests for the JSON-backed settings manager."""

    def test_settings_module_exists(self):
        from core.config_manager import SettingsManager  # noqa: F401

    def test_default_settings_struct(self):
        from core.config_manager import SettingsManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = SettingsManager(config_dir=tmp)
            settings = mgr.load()
            assert "appearance" in settings
            assert "auto_update" in settings
            assert settings["appearance"] in ("dark", "light", "system")

    def test_save_and_reload(self):
        from core.config_manager import SettingsManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = SettingsManager(config_dir=tmp)
            mgr.save({"appearance": "dark", "auto_update": False})

            mgr2 = SettingsManager(config_dir=tmp)
            reloaded = mgr2.load()
            assert reloaded["appearance"] == "dark"
            assert reloaded["auto_update"] is False

    def test_corrupt_json_returns_defaults(self):
        from core.config_manager import SettingsManager

        with tempfile.TemporaryDirectory() as tmp:
            # Write corrupt JSON
            with open(os.path.join(tmp, "settings.json"), "w") as f:
                f.write("{{{broken json")
            mgr = SettingsManager(config_dir=tmp)
            settings = mgr.load()
            # Should return defaults, not crash
            assert "appearance" in settings


# ═══════════════════════════════════════════════════════════════════════════
# 3. GUI PANEL MODULES (importability contracts)
# ═══════════════════════════════════════════════════════════════════════════
class TestPanelModules:
    """Verify that modular panel files exist and expose the right classes."""

    def test_live_console_module_importable(self):
        """live_console module must be importable (stub in v4.0)."""
        import gui.panels.live_console  # noqa: F401

    def test_settings_modal_module_exists(self):
        from gui.panels.settings_modal import SettingsModal  # noqa: F401

    def test_control_panel_module_importable(self):
        """control_panel module must be importable (stub in v4.0)."""
        import gui.panels.control_panel  # noqa: F401

    def test_main_app_module_exists(self):
        """gui/app.py must expose BOTExrateApp (v4.0 main window)."""
        from gui.app import BOTExrateApp  # noqa: F401
