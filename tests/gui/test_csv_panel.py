#!/usr/bin/env python3
"""
tests/gui/test_csv_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/csv_panel.py (CSVPanel).

These tests exercise:
  1. Widget tree construction — expected child widgets are created.
  2. SafePanel mixin contract — _destroyed flag lifecycle.
  3. Label text mutation — CTkLabel.configure() works post-creation.
  4. Post-destroy resilience — _safe_after no-ops after destroy().

All tests require a display; the tk_root fixture skips them on headless CI.
No real filesystem I/O or network calls are made.
"""

import threading

import pytest

pytestmark = pytest.mark.gui


class TestCSVPanelWidgetTree:
    """CSVPanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_import_label_attribute_exists(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert hasattr(panel, "_lbl_csv"), "_lbl_csv label must exist"
        panel.destroy()

    def test_export_label_attribute_exists(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert hasattr(panel, "_lbl_csv_export"), "_lbl_csv_export label must exist"
        panel.destroy()

    def test_both_labels_are_ctk_label_instances(self, tk_root):
        import customtkinter as ctk

        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert isinstance(panel._lbl_csv, ctk.CTkLabel)
        assert isinstance(panel._lbl_csv_export, ctk.CTkLabel)
        panel.destroy()


class TestCSVPanelLabelMutation:
    """CTkLabel.configure(text=...) updates state correctly post-creation."""

    def test_import_label_accepts_text_update(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel._lbl_csv.configure(text="Test import status")
        # CTkLabel stores text in cget; verify round-trip
        assert panel._lbl_csv.cget("text") == "Test import status"
        panel.destroy()

    def test_export_label_accepts_text_update(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel._lbl_csv_export.configure(text="Test export status")
        assert panel._lbl_csv_export.cget("text") == "Test export status"
        panel.destroy()

    def test_label_starts_empty(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel._lbl_csv.cget("text") == ""
        assert panel._lbl_csv_export.cget("text") == ""
        panel.destroy()


class TestCSVPanelSafePanelMixin:
    """SafePanel mixin (_destroyed flag + _safe_after) behaves correctly."""

    def test_destroyed_flag_starts_false(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert panel._destroyed is False
        panel.destroy()

    def test_destroyed_flag_flips_on_destroy(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel.destroy()
        assert panel._destroyed is True

    def test_safe_after_noop_post_destroy(self, tk_root):
        """_safe_after must not raise after the widget has been destroyed."""
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel.destroy()

        called = []
        # This must silently do nothing — no TclError / RuntimeError
        panel._safe_after(0, lambda: called.append(1))

        assert called == [], "_safe_after must be a no-op post-destroy"


# ── Finding: CSV Import/Export buttons stay enabled during the operation ──
class TestCSVPanelBusyLock:
    """Both CSV buttons disable on click and re-enable on completion."""

    def test_buttons_exist_and_start_enabled(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        assert hasattr(panel, "_btn_import")
        assert hasattr(panel, "_btn_export")
        assert panel._btn_import.cget("state") == "normal"
        assert panel._btn_export.cget("state") == "normal"
        panel.destroy()

    def test_set_buttons_enabled_toggles_both(self, tk_root):
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        panel._set_buttons_enabled(False)
        assert panel._btn_import.cget("state") == "disabled"
        assert panel._btn_export.cget("state") == "disabled"
        panel._set_buttons_enabled(True)
        assert panel._btn_import.cget("state") == "normal"
        assert panel._btn_export.cget("state") == "normal"
        panel.destroy()

    def test_import_click_disables_buttons_then_re_enables(
        self, tk_root, monkeypatch
    ):
        """Clicking Import disables BOTH buttons so a double-click / a
        concurrent Export cannot re-fire a worker mid-operation; completion
        re-enables them.

        The worker thread is run inline (Thread.start -> target()) so the
        post-completion _safe_after callback is queued from the main thread
        and dispatched deterministically by panel.update() — avoiding flaky
        cross-thread Tk scheduling in the test.
        """
        import core.csv_import as csv_import_mod
        import core.database as db_mod
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)

        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename", lambda **kw: "/tmp/fake.csv"
        )

        # Capture button state at the moment the worker body runs, proving
        # the disable happened before any worker could fire.
        seen = {}

        def _fake_import(path, cache):
            seen["import_state"] = panel._btn_import.cget("state")
            seen["export_state"] = panel._btn_export.cget("state")
            return 5

        class _StubCache:
            # The success path summarizes the cache; return empty so the
            # plain-count fallback runs (this test only asserts button state).
            def get_all_multi_rates(self):
                return []

        monkeypatch.setattr(csv_import_mod, "import_bot_csv", _fake_import)
        monkeypatch.setattr(db_mod, "get_cache", lambda: _StubCache())

        # Run the worker target inline on the main thread.
        def _inline_thread(target=None, daemon=None, name=None):
            class _T:
                def start(self_):
                    target()
            return _T()

        monkeypatch.setattr(threading, "Thread", _inline_thread)

        panel._on_import_csv()
        # Inside the worker both buttons were disabled.
        assert seen["import_state"] == "disabled"
        assert seen["export_state"] == "disabled"

        # Pump the loop so the queued re-enable callback runs.
        for _ in range(20):
            panel.update()
            if panel._btn_import.cget("state") == "normal":
                break
        assert panel._btn_import.cget("state") == "normal"
        assert panel._btn_export.cget("state") == "normal"
        panel.destroy()


# ── Finding: Raw exception strings shown to non-technical users ──
class TestCSVPanelHumanizedErrors:
    """_humanize_csv_error maps failure classes to plain-language guidance."""

    def test_locked_file_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = PermissionError(13, "Permission denied")
        msg = _humanize_csv_error("Import", exc)
        assert "open in another program" in msg
        assert "Import" in msg
        # The raw errno text must NOT leak to the user.
        assert "Errno 13" not in msg
        assert "Permission denied" not in msg

    def test_windows_sharing_violation_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = OSError("[WinError 32] being used by another process")
        msg = _humanize_csv_error("Export", exc)
        assert "open in another program" in msg
        assert "WinError" not in msg

    def test_file_not_found_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = FileNotFoundError(2, "No such file")
        msg = _humanize_csv_error("Import", exc)
        assert "could not be found" in msg
        assert "No such file" not in msg

    def test_bad_format_value_error_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = ValueError("Unrecognized CSV columns: ['foo', 'bar']")
        msg = _humanize_csv_error("Import", exc)
        assert "CSV format wasn't recognized" in msg
        assert "foo" not in msg

    def test_key_error_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = KeyError("Period")
        msg = _humanize_csv_error("Import", exc)
        assert "CSV format wasn't recognized" in msg

    def test_generic_os_error_message(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = OSError("disk I/O error")
        msg = _humanize_csv_error("Export", exc)
        assert "could not be read or written" in msg
        assert "disk I/O error" not in msg

    def test_unknown_error_falls_back_to_generic(self):
        from gui.panels.csv_panel import _humanize_csv_error

        exc = RuntimeError("some internal detail")
        msg = _humanize_csv_error("Import", exc)
        assert "unexpected error" in msg
        assert "some internal detail" not in msg

    def test_import_failure_shows_humanized_text_and_logs_raw(
        self, tk_root, monkeypatch, caplog
    ):
        """A failed import shows a humanized label and logs the raw error."""
        import logging

        import core.csv_import as csv_import_mod
        import core.database as db_mod
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename", lambda **kw: "/tmp/fake.csv"
        )

        def _boom(path, cache):
            raise ValueError("Unrecognized CSV columns: secret_detail")

        monkeypatch.setattr(csv_import_mod, "import_bot_csv", _boom)
        monkeypatch.setattr(db_mod, "get_cache", lambda: object())

        def _inline_thread(target=None, daemon=None, name=None):
            class _T:
                def start(self_):
                    target()
            return _T()

        monkeypatch.setattr(threading, "Thread", _inline_thread)

        with caplog.at_level(logging.ERROR):
            panel._on_import_csv()
            # Flush queued after(0) callbacks (error label + re-enable).
            for _ in range(20):
                panel.update_idletasks()
                panel.update()

        label_text = panel._lbl_csv.cget("text")
        assert "CSV format wasn't recognized" in label_text
        assert "secret_detail" not in label_text
        # Raw detail preserved in the log only.
        assert any("secret_detail" in rec.getMessage() for rec in caplog.records)
        # Buttons re-enabled after failure.
        assert panel._btn_import.cget("state") == "normal"
        assert panel._btn_export.cget("state") == "normal"
        panel.destroy()


# ── Finding: Export of an empty cache shows success-green '0 rows' ──
class TestCSVPanelExportEmptyCache:
    """count==0 export uses warning styling + an 'empty cache' message,
    not the success-green '✓ Exported 0 rate rows'."""

    def _inline_thread_factory(self):
        def _inline_thread(target=None, daemon=None, name=None):
            class _T:
                def start(self_):
                    target()
            return _T()
        return _inline_thread

    def test_export_zero_rows_uses_warning_not_success(
        self, tk_root, monkeypatch
    ):
        import core.csv_export as csv_export_mod
        import core.database as db_mod
        from gui.panels.csv_panel import CSVPanel
        from gui.theme import get_theme

        panel = CSVPanel(tk_root)
        monkeypatch.setattr(
            "tkinter.filedialog.asksaveasfilename",
            lambda **kw: "/tmp/fake_export.csv",
        )
        # Empty cache => exporter reports 0 rows written.
        monkeypatch.setattr(
            csv_export_mod, "export_rates_csv", lambda path, cache: 0
        )
        monkeypatch.setattr(db_mod, "get_cache", lambda: object())
        monkeypatch.setattr(
            threading, "Thread", self._inline_thread_factory()
        )

        panel._on_export_csv()
        for _ in range(20):
            panel.update_idletasks()
            panel.update()

        t = get_theme()
        label = panel._lbl_csv_export
        # Not the success-green color; uses the warning token instead.
        assert label.cget("text_color") == t["warning"]
        assert label.cget("text_color") != t["modal_success"]
        # The message no longer reads as a successful "✓ Exported 0 rows".
        text = label.cget("text")
        assert "0 rate rows" not in text
        panel.destroy()

    def test_export_nonzero_rows_keeps_success_styling(
        self, tk_root, monkeypatch
    ):
        import core.csv_export as csv_export_mod
        import core.database as db_mod
        from gui.panels.csv_panel import CSVPanel
        from gui.theme import get_theme

        panel = CSVPanel(tk_root)
        monkeypatch.setattr(
            "tkinter.filedialog.asksaveasfilename",
            lambda **kw: "/tmp/fake_export.csv",
        )
        monkeypatch.setattr(
            csv_export_mod, "export_rates_csv", lambda path, cache: 42
        )
        monkeypatch.setattr(db_mod, "get_cache", lambda: object())
        monkeypatch.setattr(
            threading, "Thread", self._inline_thread_factory()
        )

        panel._on_export_csv()
        for _ in range(20):
            panel.update_idletasks()
            panel.update()

        t = get_theme()
        label = panel._lbl_csv_export
        assert label.cget("text_color") == t["modal_success"]
        assert "42" in label.cget("text")
        panel.destroy()


# ── Finding: Import success gives no date span / currencies / next step ──
class TestCSVPanelImportSummary:
    """A successful import reports the date span and currencies now cached
    (so the offline-fallback user knows the right data loaded)."""

    def _inline_thread_factory(self):
        def _inline_thread(target=None, daemon=None, name=None):
            class _T:
                def start(self_):
                    target()
            return _T()
        return _inline_thread

    def test_summarize_cache_returns_span_and_currencies(self):
        from decimal import Decimal

        from gui.panels.csv_panel import _summarize_cache

        class _FakeCache:
            def get_all_multi_rates(self):
                # Out-of-order on purpose is not needed: exporter orders by
                # date ASC, so first/last bound the span.
                return [
                    ("2026-01-01", "USD", "buying_transfer", Decimal("35.1")),
                    ("2026-03-15", "EUR", "selling", Decimal("38.0")),
                    ("2026-06-04", "USD", "selling", Decimal("36.2")),
                ]

        result = _summarize_cache(_FakeCache())
        assert result is not None
        lo, hi, currencies = result
        assert lo == "2026-01-01"
        assert hi == "2026-06-04"
        # Distinct + sorted.
        assert currencies == "EUR, USD"

    def test_summarize_cache_empty_returns_none(self):
        from gui.panels.csv_panel import _summarize_cache

        class _EmptyCache:
            def get_all_multi_rates(self):
                return []

        assert _summarize_cache(_EmptyCache()) is None

    def test_import_success_label_includes_span_and_currencies(
        self, tk_root, monkeypatch
    ):
        from decimal import Decimal

        import core.csv_import as csv_import_mod
        import core.database as db_mod
        import core.i18n as i18n_mod
        from gui.panels.csv_panel import CSVPanel

        # Register the wave-2-owned detail key locally so this test is
        # deterministic regardless of whether the i18n catalog has been
        # populated yet (tr() falls back to the bare key otherwise, which
        # carries no {placeholders} and would drop the substituted values).
        patched_catalog = dict(i18n_mod.CATALOG)
        patched_catalog["csv.import_ok_detail"] = {
            "en": (
                "✓ Imported {count} entries: {currencies} — {start} to {end}. "
                "These rates are now used automatically by Process Batch."
            ),
        }
        monkeypatch.setattr(i18n_mod, "CATALOG", patched_catalog)

        panel = CSVPanel(tk_root)
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename", lambda **kw: "/tmp/fake.csv"
        )

        class _FakeCache:
            def get_all_multi_rates(self):
                return [
                    ("2026-01-01", "USD", "buying_transfer", Decimal("35.1")),
                    ("2026-06-04", "EUR", "selling", Decimal("38.0")),
                ]

        monkeypatch.setattr(
            csv_import_mod, "import_bot_csv", lambda path, cache: 124
        )
        monkeypatch.setattr(db_mod, "get_cache", lambda: _FakeCache())
        monkeypatch.setattr(
            threading, "Thread", self._inline_thread_factory()
        )

        panel._on_import_csv()
        for _ in range(20):
            panel.update_idletasks()
            panel.update()

        text = panel._lbl_csv.cget("text")
        # The detail key carries the span, the distinct currencies, the count
        # and the next-step hint.
        assert "2026-01-01" in text
        assert "2026-06-04" in text
        assert "USD" in text
        assert "EUR" in text
        assert "124" in text
        assert "Process Batch" in text
        panel.destroy()

    def test_import_success_with_empty_cache_falls_back_to_count_only(
        self, tk_root, monkeypatch
    ):
        """If the cache somehow reports empty (e.g. a no-op CSV), the label
        falls back to the plain count message rather than crashing."""
        import core.csv_import as csv_import_mod
        import core.database as db_mod
        from gui.panels.csv_panel import CSVPanel

        panel = CSVPanel(tk_root)
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename", lambda **kw: "/tmp/fake.csv"
        )

        class _EmptyCache:
            def get_all_multi_rates(self):
                return []

        monkeypatch.setattr(
            csv_import_mod, "import_bot_csv", lambda path, cache: 0
        )
        monkeypatch.setattr(db_mod, "get_cache", lambda: _EmptyCache())
        monkeypatch.setattr(
            threading, "Thread", self._inline_thread_factory()
        )

        panel._on_import_csv()
        for _ in range(20):
            panel.update_idletasks()
            panel.update()

        # Must not raise; label shows the plain count fallback.
        assert "0" in panel._lbl_csv.cget("text")
        panel.destroy()
