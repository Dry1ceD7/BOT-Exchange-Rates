#!/usr/bin/env python3
"""
tests/test_engine_factory.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Engine Factory Tests
---------------------------------------------------------------------------
Validates that the single OpenpyxlEngine is properly exposed and
inherits from BaseEngine with the required contract methods.
"""

from unittest.mock import patch


class TestEngineFactoryRouting:
    """Verify that the factory returns the correct engine class."""

    def test_factory_module_exists(self):
        """The engine_factory module must be importable."""
        from core.engine_factory import get_engine  # noqa: F401

    def test_base_engine_interface_exists(self):
        """A BaseEngine abstract class must exist with required methods."""
        from core.engine_factory import BaseEngine

        assert hasattr(BaseEngine, "process_ledger")
        assert hasattr(BaseEngine, "process_batch")

    def test_all_platforms_return_openpyxl_engine(self):
        """All platforms now return OpenpyxlEngine (COM removed)."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "OpenpyxlEngine"

    @patch("sys.platform", "win32")
    def test_windows_returns_openpyxl_engine(self):
        """On Windows, the factory returns OpenpyxlEngine (COM removed)."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "OpenpyxlEngine"

    @patch("sys.platform", "darwin")
    def test_macos_returns_openpyxl_engine(self):
        """On macOS, the factory returns OpenpyxlEngine."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "OpenpyxlEngine"

    @patch("sys.platform", "linux")
    def test_linux_returns_openpyxl_engine(self):
        """On Linux, the factory returns OpenpyxlEngine."""
        from core.engine_factory import get_engine_class

        engine_cls = get_engine_class()
        assert engine_cls.__name__ == "OpenpyxlEngine"


class TestBaseEngineContract:
    """Verify that OpenpyxlEngine shares the BaseEngine API."""

    def test_openpyxl_engine_inherits_base(self):
        """OpenpyxlEngine must be a subclass of BaseEngine."""
        from core.engine_factory import BaseEngine, OpenpyxlEngine

        assert issubclass(OpenpyxlEngine, BaseEngine)

    def test_openpyxl_engine_has_process_ledger(self):
        """OpenpyxlEngine must implement process_ledger()."""
        from core.engine_factory import OpenpyxlEngine

        assert callable(getattr(OpenpyxlEngine, "process_ledger", None))

    def test_openpyxl_engine_has_process_batch(self):
        """OpenpyxlEngine must implement process_batch()."""
        from core.engine_factory import OpenpyxlEngine

        assert callable(getattr(OpenpyxlEngine, "process_batch", None))


class TestAutoUpdaterContract:
    """Verify the auto-updater module exists and exposes the right API."""

    def test_auto_updater_module_exists(self):
        """The auto_updater module must be importable."""
        from core.auto_updater import check_for_update  # noqa: F401

    def test_check_for_update_returns_dict(self):
        """check_for_update() must return a dict with version info."""
        from core.auto_updater import check_for_update

        result = check_for_update(current_version="0.0.0-test")
        assert isinstance(result, dict)
        assert "update_available" in result
