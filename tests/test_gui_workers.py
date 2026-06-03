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

    def test_live_console_module_exists(self):
        from gui.panels.live_console import LiveConsolePanel  # noqa: F401

    def test_settings_modal_module_exists(self):
        from gui.panels.settings_modal import SettingsModal  # noqa: F401

    def test_control_panel_module_exists(self):
        from gui.panels.control_panel import ControlPanel  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════════
# 4. gui/app.py PURE HELPERS (no Tk root required)
# ═══════════════════════════════════════════════════════════════════════════
class TestAppExtensionResolution:
    """Lock the single source of truth for supported Excel extensions."""

    def test_single_extensions_constant(self):
        """EXCEL_EXTENSIONS is the only supported-extension constant.

        The duplicate OPENPYXL_NATIVE alias was dead code and must stay gone.
        """
        import gui.app as app

        assert app.EXCEL_EXTENSIONS == (".xlsx", ".xlsm")
        assert not hasattr(app, "OPENPYXL_NATIVE")

    def test_resolve_filters_to_excel(self, tmp_path):
        from gui.app import resolve_excel_files

        keep = tmp_path / "ledger.xlsx"
        keep.write_text("x")
        macro = tmp_path / "macro.xlsm"
        macro.write_text("x")
        (tmp_path / "notes.txt").write_text("x")

        resolved = resolve_excel_files([str(keep), str(macro),
                                        str(tmp_path / "notes.txt")])
        names = sorted(p.rsplit("/", 1)[-1] for p in resolved)
        assert names == ["ledger.xlsx", "macro.xlsm"]

    def test_resolve_collects_rejected_spreadsheets(self, tmp_path):
        from gui.app import resolve_excel_files

        ok = tmp_path / "a.xlsx"
        ok.write_text("x")
        legacy = tmp_path / "old.xls"
        legacy.write_text("x")

        accepted, rejected = resolve_excel_files(
            [str(ok), str(legacy)], collect_rejected=True,
        )
        assert [p.rsplit("/", 1)[-1] for p in accepted] == ["a.xlsx"]
        assert [p.rsplit("/", 1)[-1] for p in rejected] == ["old.xls"]
