#!/usr/bin/env python3
"""
tests/gui/test_app_batch_state.py
---------------------------------------------------------------------------
Targeted tests for the batch-running UI-state guards on BOTExrateApp.

Constructing a real BOTExrateApp would spawn a second Tk interpreter alongside
the session-scoped tk_root (segfaults CTk on macOS/aarch64), so — mirroring
test_app_shutdown.py — we invoke the unbound methods against a minimal
stand-in `self` carrying only the attributes each method touches.

Covers three confirmed audit findings:

  1. Drop zone / browse / queue must NOT re-enable Process Batch mid-run; they
     short-circuit with a busy status while a batch is active, and the UI is
     re-enabled on complete/error.
  2. _show_batch_complete renders WHICH files failed and WHY (an always-visible
     box) when failures > 0, and hides it when all succeed.
  3. A scheduler-fired batch locks the SAME controls a manual run does and
     reflects the run in lbl_status (never silently leaves buttons enabled).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.gui


def _btn():
    """A button stand-in that records its last configured state."""
    b = MagicMock()
    return b


def _descendant_labels(widget):
    """Recursively collect every CTkLabel under ``widget``."""
    import customtkinter as ctk

    found = []
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkLabel):
            found.append(child)
        found.extend(_descendant_labels(child))
    return found


def _make_app(batch_running=False, file_queue=None):
    """Minimal stand-in carrying the attrs the batch-state methods touch."""
    from gui.app import BOTExrateApp

    app = SimpleNamespace(
        _batch_running=batch_running,
        # Scheduler-fired runs flip this on; a manual run (the case these tests
        # cover) leaves it off so the completion/error paths skip the tray
        # notification + auto-restore branch.
        _scheduled_run_active=False,
        _revert_running=False,
        _tray=None,
        _failed_box=None,
        file_queue=file_queue if file_queue is not None else [],
        last_processed_path=None,
        btn_process=_btn(),
        btn_revert=_btn(),
        btn_export_exrate=_btn(),
        btn_reveal=_btn(),
        lbl_status=MagicMock(),
        lbl_queue=MagicMock(),
        dz_text=MagicMock(),
        dz_sub=MagicMock(),
        progressbar=MagicMock(),
        batch_handler=MagicMock(),
        update_idletasks=MagicMock(),
    )
    # Bind the real busy-status / unlock helpers so guarded methods that call
    # back into them exercise the genuine control flow on the stand-in.
    app._flash_busy_status = lambda: BOTExrateApp._flash_busy_status(app)
    app._unlock_ui_after_batch = lambda: BOTExrateApp._unlock_ui_after_batch(app)
    app._lock_ui_for_batch = lambda: BOTExrateApp._lock_ui_for_batch(app)
    return app


# ---------------------------------------------------------------------------
# Finding 1 — drop zone / browse / queue guarded while a batch runs
# ---------------------------------------------------------------------------

class TestQueueGuardsWhileRunning:
    def test_set_queue_ignored_while_batch_running(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["old.xlsx"])
        BOTExrateApp._set_queue(app, ["new1.xlsx", "new2.xlsx"])

        # Selection unchanged and Process Batch never re-enabled mid-run.
        assert app.file_queue == ["old.xlsx"]
        app.btn_process.configure.assert_not_called()
        # The operator is told the UI is busy rather than silently ignored.
        app.lbl_status.configure.assert_called_once()
        assert "Busy" in app.lbl_status.configure.call_args.kwargs["text"]

    def test_set_queue_updates_when_idle(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False)
        BOTExrateApp._set_queue(app, ["a.xlsx", "b.xlsx"])

        assert app.file_queue == ["a.xlsx", "b.xlsx"]
        app.btn_process.configure.assert_any_call(state="normal")

    def test_on_drop_ignored_while_batch_running(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["old.xlsx"])
        # _set_queue must not be reached; patch it to prove it isn't called.
        called = {"set_queue": False}
        app._set_queue = lambda files: called.__setitem__("set_queue", True)

        event = SimpleNamespace(data="some_dropped.xlsx")
        BOTExrateApp._on_drop(app, event)

        assert called["set_queue"] is False
        assert app.file_queue == ["old.xlsx"]
        assert "Busy" in app.lbl_status.configure.call_args.kwargs["text"]

    def test_browse_files_ignored_while_batch_running(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)

        with pytest.MonkeyPatch.context() as mp:
            picker = MagicMock(return_value=("picked.xlsx",))
            mp.setattr("gui.app.filedialog.askopenfilenames", picker)
            BOTExrateApp._browse_files(app)

        # The file dialog must never even open while a batch is running.
        picker.assert_not_called()
        assert "Busy" in app.lbl_status.configure.call_args.kwargs["text"]


# ---------------------------------------------------------------------------
# lock / unlock helpers used by both manual and scheduled runs
# ---------------------------------------------------------------------------

class TestLockUnlockHelpers:
    def test_lock_disables_all_action_buttons_and_sets_flag(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False)
        BOTExrateApp._lock_ui_for_batch(app)

        assert app._batch_running is True
        app.btn_process.configure.assert_any_call(state="disabled")
        app.btn_revert.configure.assert_any_call(state="disabled")
        app.btn_export_exrate.configure.assert_any_call(state="disabled")

    def test_unlock_reenables_all_action_buttons_and_clears_flag(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        BOTExrateApp._unlock_ui_after_batch(app)

        assert app._batch_running is False
        app.btn_process.configure.assert_any_call(state="normal")
        app.btn_revert.configure.assert_any_call(state="normal")
        app.btn_export_exrate.configure.assert_any_call(state="normal")

    def test_show_error_unlocks_ui(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        BOTExrateApp._show_error(app, "boom")

        assert app._batch_running is False
        app.btn_process.configure.assert_any_call(state="normal")
        app.btn_export_exrate.configure.assert_any_call(state="normal")


# ---------------------------------------------------------------------------
# Finding 2 — completion summary renders WHICH files failed and WHY
# ---------------------------------------------------------------------------

class TestBatchCompleteShowsFailures:
    def test_complete_unlocks_and_renders_failures(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["a.xlsx"])
        rendered = {}
        app._render_failed_files = lambda errors: rendered.__setitem__("errors", errors)

        errors = [
            "jan.xlsx: BACKUP FAILED — skipped (disk full)",
            "feb.xlsx: <ERROR: No Rate>",
            "mar.xlsx: file exceeds 15MB limit",
        ]
        BOTExrateApp._show_batch_complete(app, success=7, fail=3, errors=errors)

        # UI re-enabled and the failure detail is forwarded for rendering.
        assert app._batch_running is False
        assert rendered["errors"] == errors
        # The status line must point the user at the failed-files detail.
        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert "3 failed" in status

    def test_complete_all_success_hides_failure_box(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["a.xlsx"])
        rendered = {}
        app._render_failed_files = lambda errors: rendered.__setitem__("errors", errors)

        BOTExrateApp._show_batch_complete(app, success=5, fail=0, errors=[])

        assert rendered["errors"] == []
        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert "successfully" in status

    def test_render_failed_files_builds_one_row_per_failure(self, tk_root):
        """Render against a real CTk card so we exercise the actual widget
        build, then count the rows created in the scrollable frame."""
        import customtkinter as ctk

        from gui.app import BOTExrateApp

        card = ctk.CTkFrame(tk_root)
        app = SimpleNamespace(card=card, _failed_box=None)

        errors = [
            "jan.xlsx: BACKUP FAILED — skipped (disk full)",
            "feb.xlsx: <ERROR: No Rate>",
        ]
        BOTExrateApp._render_failed_files(app, errors)

        assert app._failed_box is not None
        # CTk nests the scrollable frame inside an internal parent, so walk the
        # whole subtree to find the per-failure bullet rows.
        bullets = [
            lbl.cget("text")
            for lbl in _descendant_labels(app._failed_box)
            if lbl.cget("text").startswith("•")
        ]
        assert len(bullets) == len(errors)
        # Every failure reason is shown verbatim — file name AND why.
        shown = " ".join(bullets)
        assert "jan.xlsx" in shown
        assert "No Rate" in shown
        card.destroy()

    def test_render_failed_files_empty_hides_box(self, tk_root):
        import customtkinter as ctk

        from gui.app import BOTExrateApp

        card = ctk.CTkFrame(tk_root)
        prior = ctk.CTkFrame(card)
        app = SimpleNamespace(card=card, _failed_box=prior)

        BOTExrateApp._render_failed_files(app, [])

        # An empty error list tears down any prior box and shows nothing new.
        assert app._failed_box is None
        card.destroy()

    def test_render_failed_files_replaces_prior_box(self, tk_root):
        """A second failing batch must not stack boxes."""
        import customtkinter as ctk

        from gui.app import BOTExrateApp

        card = ctk.CTkFrame(tk_root)
        app = SimpleNamespace(card=card, _failed_box=None)

        BOTExrateApp._render_failed_files(app, ["a.xlsx: boom"])
        first = app._failed_box
        BOTExrateApp._render_failed_files(app, ["b.xlsx: kaboom"])
        second = app._failed_box

        assert second is not first
        # Only one failed box exists on the card at a time.
        boxes = [
            w for w in card.winfo_children()
            if isinstance(w, ctk.CTkFrame) and w is second
        ]
        assert len(boxes) == 1
        card.destroy()


# ---------------------------------------------------------------------------
# Finding 3 — scheduler-fired batch locks the same controls as a manual run
# ---------------------------------------------------------------------------

class TestScheduledBatchLocksUI:
    def test_scheduled_batch_locks_buttons_and_updates_status(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False)

        BOTExrateApp._begin_scheduled_batch(app, ["x.xlsx", "y.xlsx"], "2026-01-01")

        # Same controls a manual run locks.
        assert app._batch_running is True
        app.btn_process.configure.assert_any_call(state="disabled")
        app.btn_revert.configure.assert_any_call(state="disabled")
        app.btn_export_exrate.configure.assert_any_call(state="disabled")
        # The run is visibly reflected — not a silent "Ready".
        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert "Scheduled run" in status
        # The batch was actually dispatched with the scheduled files.
        app.batch_handler.start_batch.assert_called_once_with(
            ["x.xlsx", "y.xlsx"], "2026-01-01",
        )

    def test_scheduled_batch_skips_when_already_running(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)

        BOTExrateApp._begin_scheduled_batch(app, ["x.xlsx"], "2026-01-01")

        # Must not clobber an in-flight manual run's locked state nor dispatch.
        app.batch_handler.start_batch.assert_not_called()
        app.lbl_status.configure.assert_not_called()
