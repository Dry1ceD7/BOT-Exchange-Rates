#!/usr/bin/env python3
"""
tests/gui/test_app_shutdown.py
---------------------------------------------------------------------------
Targeted tests for BOTExrateApp behaviours that do NOT require a live second
CTk root (constructing a real BOTExrateApp would spawn a second Tk
interpreter alongside the session-scoped tk_root, which segfaults CTk on
macOS/aarch64). Instead we invoke the unbound methods against a minimal
stand-in `self`, exercising the exact control flow:

  1. _show_download_error exists, is wired by the settings modal, and both
     pops a native error AND updates the status label (never silent).
  2. _on_app_close closes the rate-ticker CacheDB after joining workers.

These are structural/behavioural checks; the SimpleNamespace stand-in carries
only the attributes each method touches.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# 1. _show_download_error exists + wired + not silent
# ---------------------------------------------------------------------------

class TestShowDownloadError:
    """_show_download_error surfaces failures via popup + status label."""

    def test_method_exists_on_class(self):
        from gui.app import BOTExrateApp

        assert hasattr(BOTExrateApp, "_show_download_error")
        assert callable(BOTExrateApp._show_download_error)

    def test_pops_native_error_and_updates_label(self):
        from gui.app import BOTExrateApp

        fake = SimpleNamespace(lbl_status=MagicMock())
        with patch("gui.app.messagebox.showerror") as mock_box:
            BOTExrateApp._show_download_error(fake, "Install failed: boom")

        mock_box.assert_called_once()
        # The message text carries the underlying error — never silent.
        assert "boom" in mock_box.call_args[0][1]
        fake.lbl_status.configure.assert_called_once()

    def test_settings_modal_wires_show_download_error(self, tk_root):
        """The settings modal must pass the app's _show_download_error to
        VersionPanel(on_error=...) — fix #4 makes this resolve to a real
        method instead of None. We capture the kwarg the modal actually
        passes during _build_ui."""
        from unittest.mock import MagicMock as MM

        from gui.panels.settings_modal import SettingsModal

        defaults = {
            "appearance": "system",
            "auto_update": True,
            "rate_type": "buying_transfer",
            "anomaly_threshold_pct": 5.0,
        }
        mock_mgr = MM()
        mock_mgr.load.return_value = dict(defaults)

        captured = {}

        class _FakePanel:
            def __init__(self, master, on_restart=None, on_error=None, **kw):
                captured["on_error"] = on_error

            def pack(self, *a, **k):
                return self

        # Stub the app-side hook on tk_root (the modal's master) so the
        # getattr inside _build_ui resolves it, mirroring BOTExrateApp.
        marker = MM(name="_show_download_error")
        tk_root._show_download_error = marker
        try:
            with (
                patch("gui.panels.settings_modal.SettingsManager",
                      return_value=mock_mgr),
                patch("gui.panels.version_panel.VersionPanel", _FakePanel),
                patch("gui.panels.csv_panel.CSVPanel", _FakePanel),
            ):
                modal = SettingsModal(tk_root)
        finally:
            del tk_root._show_download_error

        assert captured.get("on_error") is marker, (
            "settings modal must wire on_error to app._show_download_error, "
            "not None"
        )
        modal.destroy()


# ---------------------------------------------------------------------------
# 2. _on_app_close closes the rate-ticker CacheDB
# ---------------------------------------------------------------------------

class TestOnAppCloseClosesCache:
    """_on_app_close must close self._cache_db after joining workers."""

    def _fake_self(self, cache_db):
        """Minimal stand-in carrying only attrs _on_app_close touches."""
        return SimpleNamespace(
            _closing=False,
            rate_ticker=None,
            console=None,
            _updater=None,
            _tray=None,
            thread_registry=None,
            _cache_db=cache_db,
            destroy=MagicMock(),
            _on_scheduler_stop=MagicMock(),
        )

    def test_cache_db_closed_on_shutdown(self):
        from gui.app import BOTExrateApp

        cache_db = MagicMock()
        fake = self._fake_self(cache_db)
        BOTExrateApp._on_app_close(fake)

        cache_db.close.assert_called_once()
        fake.destroy.assert_called_once()

    def test_no_error_when_cache_db_is_none(self):
        from gui.app import BOTExrateApp

        fake = self._fake_self(None)
        # Must not raise when there is no cache (ticker init failed path).
        BOTExrateApp._on_app_close(fake)
        fake.destroy.assert_called_once()

    def test_cache_close_failure_is_swallowed(self):
        from gui.app import BOTExrateApp

        cache_db = MagicMock()
        cache_db.close.side_effect = OSError("disk gone")
        fake = self._fake_self(cache_db)
        # A failing close must not block the Tk root teardown.
        BOTExrateApp._on_app_close(fake)
        fake.destroy.assert_called_once()
