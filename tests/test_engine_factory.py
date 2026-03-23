#!/usr/bin/env python3
"""
tests/test_engine_factory.py
---------------------------------------------------------------------------
TDGD RED PHASE: Failing Tests for the Engine Factory (Strategy Pattern)
---------------------------------------------------------------------------
These tests MUST FAIL initially because core/engine_factory.py does not
exist yet. They define the contract that the factory must satisfy.
"""

from unittest.mock import patch


class TestEngineFactoryRouting:
    """Verify that the OS-level router returns the correct engine class."""

    def test_factory_module_exists(self):
        """The engine_factory module must be importable."""
        from core.engine_factory import get_engine  # noqa: F401

    def test_base_engine_interface_exists(self):
        """A BaseEngine abstract class must exist with required methods."""
        from core.engine_factory import BaseEngine

        assert hasattr(BaseEngine, "process_ledger")
        assert hasattr(BaseEngine, "process_batch")

    @patch("sys.platform", "win32")
    def test_windows_returns_native_engine(self):
        """On Windows, the factory must return NativeExcelEngine."""
        # Force re-import with patched platform
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "NativeExcelEngine"

    @patch("sys.platform", "darwin")
    def test_macos_returns_fallback_engine(self):
        """On macOS, the factory must return FallbackExcelEngine."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "FallbackExcelEngine"

    @patch("sys.platform", "linux")
    def test_linux_returns_fallback_engine(self):
        """On Linux, the factory must return FallbackExcelEngine."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "FallbackExcelEngine"


class TestBaseEngineContract:
    """Verify that both engine implementations share the BaseEngine API."""

    def test_fallback_engine_inherits_base(self):
        """FallbackExcelEngine must be a subclass of BaseEngine."""
        from core.engine_factory import BaseEngine, FallbackExcelEngine

        assert issubclass(FallbackExcelEngine, BaseEngine)

    def test_fallback_engine_has_process_ledger(self):
        """FallbackExcelEngine must implement process_ledger()."""
        from core.engine_factory import FallbackExcelEngine

        assert callable(getattr(FallbackExcelEngine, "process_ledger", None))

    def test_fallback_engine_has_process_batch(self):
        """FallbackExcelEngine must implement process_batch()."""
        from core.engine_factory import FallbackExcelEngine

        assert callable(getattr(FallbackExcelEngine, "process_batch", None))


class TestAutoUpdaterContract:
    """Verify the auto-updater module exists and exposes the right API."""

    def test_auto_updater_module_exists(self):
        """The auto_updater module must be importable."""
        from core.auto_updater import check_for_update  # noqa: F401

    def test_check_for_update_returns_dict(self):
        """check_for_update() must return a dict with version info."""
        from core.auto_updater import check_for_update

        # With a mock, it should return a structure like:
        # {"update_available": bool, "latest_version": str, "download_url": str}
        result = check_for_update(current_version="0.0.0-test")
        assert isinstance(result, dict)
        assert "update_available" in result
