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

from core.i18n import tr

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
        _exrate_running=False,
        _tray=None,
        _failed_box=None,
        dnd_enabled=False,
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
    app._settle_progressbar = lambda value: BOTExrateApp._settle_progressbar(app, value)
    app._last_succeeded_path = (
        lambda errors: BOTExrateApp._last_succeeded_path(app, errors)
    )
    app._reset_queue_after_run = (
        lambda: BOTExrateApp._reset_queue_after_run(app)
    )
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
        assert (
            app.lbl_status.configure.call_args.kwargs["text"]
            == tr("main.status_busy")
        )

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
        assert (
            app.lbl_status.configure.call_args.kwargs["text"]
            == tr("main.status_busy")
        )

    def test_browse_files_ignored_while_batch_running(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)

        with pytest.MonkeyPatch.context() as mp:
            picker = MagicMock(return_value=("picked.xlsx",))
            mp.setattr("gui.app.filedialog.askopenfilenames", picker)
            BOTExrateApp._browse_files(app)

        # The file dialog must never even open while a batch is running.
        picker.assert_not_called()
        assert (
            app.lbl_status.configure.call_args.kwargs["text"]
            == tr("main.status_busy")
        )


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


# ---------------------------------------------------------------------------
# round-6 #2 — reveal points at a SUCCEEDING file, never a failed/skipped one
# ---------------------------------------------------------------------------

class TestRevealTargetsSucceededFile:
    def test_reveal_hidden_when_last_file_failed(self):
        from gui.app import BOTExrateApp

        # Batch of three; the LAST file failed (backup failed / no-rate). The
        # reveal must point at the last SUCCEEDING file, not the failed one.
        app = _make_app(
            batch_running=True,
            file_queue=["jan.xlsx", "feb.xlsx", "mar.xlsx"],
        )
        app._render_failed_files = MagicMock()
        errors = ["mar.xlsx: <ERROR: No Rate>"]

        BOTExrateApp._show_batch_complete(app, success=2, fail=1, errors=errors)

        # feb.xlsx is the last file that actually got written.
        assert app.last_processed_path == "feb.xlsx"
        app.btn_reveal.pack.assert_called_once()

    def test_reveal_hidden_when_all_failed(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["jan.xlsx"])
        app._render_failed_files = MagicMock()
        errors = ["jan.xlsx: BACKUP FAILED — skipped (disk full)"]

        BOTExrateApp._show_batch_complete(app, success=0, fail=1, errors=errors)

        # Nothing was written — the reveal must be hidden and target cleared.
        assert app.last_processed_path is None
        app.btn_reveal.pack_forget.assert_called()
        app.btn_reveal.pack.assert_not_called()

    def test_last_succeeded_path_skips_failed_basenames(self):

        app = _make_app(file_queue=["/a/jan.xlsx", "/a/feb.xlsx", "/a/mar.xlsx"])
        # mar failed; the reveal target is the deepest path whose basename is
        # not in the failed set — feb.xlsx (full path preserved).
        result = app._last_succeeded_path(["mar.xlsx: <ERROR: No Rate>"])
        assert result == "/a/feb.xlsx"

    def test_last_succeeded_path_none_when_queue_empty(self):

        app = _make_app(file_queue=[])
        assert app._last_succeeded_path([]) is None


# ---------------------------------------------------------------------------
# round-6 #3 — a completed manual batch clears the queue + disables Process
# ---------------------------------------------------------------------------

class TestQueueClearedAfterRun:
    def test_successful_manual_run_clears_queue_and_disables_process(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["a.xlsx", "b.xlsx"])
        app._render_failed_files = MagicMock()

        BOTExrateApp._show_batch_complete(app, success=2, fail=0, errors=[])

        # Queue emptied and Process disabled until a fresh selection is made,
        # so a stray re-click cannot silently reprocess the same files.
        assert app.file_queue == []
        app.btn_process.configure.assert_any_call(state="disabled")
        # Drop-zone copy reset to its idle prompt.
        idle_text = app.dz_text.configure.call_args.kwargs["text"]
        assert "Click to select files" in idle_text or "Drop Excel" in idle_text

    def test_scheduled_run_does_not_clear_interactive_queue(self):
        from gui.app import BOTExrateApp

        # A scheduled fire uses its own snapshot; completing it must not wipe the
        # user's pending interactive selection in self.file_queue.
        app = _make_app(batch_running=True, file_queue=["pending.xlsx"])
        app._scheduled_run_active = True
        app._render_failed_files = MagicMock()
        app._announce_scheduled_run = MagicMock()

        BOTExrateApp._show_batch_complete(app, success=1, fail=0, errors=[])

        assert app.file_queue == ["pending.xlsx"]


# ---------------------------------------------------------------------------
# round-6 #4 — a dry run reports SIMULATION and never offers a reveal
# ---------------------------------------------------------------------------

class TestDryRunCompletionCopy:
    def test_dry_run_reports_simulation_and_suppresses_reveal(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["a.xlsx", "b.xlsx"])
        app._render_failed_files = MagicMock()

        BOTExrateApp._show_batch_complete(
            app, success=2, fail=0, errors=[], dry_run=True,
        )

        # Compare against the i18n-rendered string for the active language so
        # the assertion survives a Thai/English toggle rather than pinning a
        # raw English literal (the simulation copy now routes through tr()).
        from core.i18n import plural, tr

        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert status == tr(
            "main.status_simulation", count=2, plural=plural(2),
        )
        # No reveal of an unmodified file, and the queue is NOT cleared (nothing
        # was processed — the operator may still want to run for real).
        app.btn_reveal.pack.assert_not_called()
        app.btn_reveal.pack_forget.assert_called()
        assert app.file_queue == ["a.xlsx", "b.xlsx"]


# ---------------------------------------------------------------------------
# round-6 #7 — the progress bar pulses during the first-file network wait
# ---------------------------------------------------------------------------

class TestProgressPulseDuringFetch:
    def test_lock_starts_indeterminate_pulse(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False)
        BOTExrateApp._lock_ui_for_batch(app)

        # Bar switched to indeterminate AND animated so it never looks frozen.
        app.progressbar.configure.assert_any_call(mode="indeterminate")
        app.progressbar.start.assert_called_once()

    def test_first_progress_event_settles_to_determinate(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        BOTExrateApp._update_progress(app, idx=1, total=3, fname="a.xlsx", error=None)

        # Pulse stopped, mode flipped back to determinate, real fraction set.
        app.progressbar.stop.assert_called_once()
        app.progressbar.configure.assert_any_call(mode="determinate")
        app.progressbar.set.assert_called_with(1 / 3)


# ---------------------------------------------------------------------------
# engine-preflight seam — selection-time feedback for oversized/locked files
# ---------------------------------------------------------------------------

class TestPreflightWarnSeam:
    """The engine exposed LedgerEngine.preflight_file for selection-time
    feedback; these lock in that _preflight_warn actually wires it into the
    drop/browse path so an oversized or locked file is flagged immediately
    rather than only failing mid-run."""

    def _app(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace()
        app._preflight_warn = lambda files: BOTExrateApp._preflight_warn(app, files)
        return app

    def test_oversized_file_triggers_warning(self, tmp_path):
        from core.engine import LedgerEngine
        from gui.app import BOTExrateApp

        big = tmp_path / "big.xlsx"
        big.write_bytes(b"\0" * (LedgerEngine.MAX_FILE_BYTES + 1))

        app = self._app()
        with pytest.MonkeyPatch.context() as mp:
            warn = MagicMock()
            mp.setattr("gui.app.messagebox.showwarning", warn)
            BOTExrateApp._preflight_warn(app, [str(big)])

        warn.assert_called_once()
        body = warn.call_args.args[1]
        assert "big.xlsx" in body

    def test_healthy_file_no_warning(self, tmp_path):
        from gui.app import BOTExrateApp

        # A small, writable .xlsx is fine — preflight must stay silent.
        ok = tmp_path / "ok.xlsx"
        ok.write_bytes(b"\0" * 1024)

        app = self._app()
        with pytest.MonkeyPatch.context() as mp:
            warn = MagicMock()
            mp.setattr("gui.app.messagebox.showwarning", warn)
            BOTExrateApp._preflight_warn(app, [str(ok)])

        warn.assert_not_called()

    def test_unsupported_extension_not_double_warned(self, tmp_path):
        """_on_drop already warns about unsupported extensions via
        resolve_excel_files; _preflight_warn must not duplicate it."""
        from gui.app import BOTExrateApp

        bad = tmp_path / "ledger.txt"
        bad.write_bytes(b"\0" * 1024)

        app = self._app()
        with pytest.MonkeyPatch.context() as mp:
            warn = MagicMock()
            mp.setattr("gui.app.messagebox.showwarning", warn)
            BOTExrateApp._preflight_warn(app, [str(bad)])

        warn.assert_not_called()

    def test_on_drop_runs_preflight_before_set_queue(self, tmp_path):
        """The drop path must call _preflight_warn on the resolved Excel files
        and still populate the queue (advisory, non-blocking)."""
        from gui.app import BOTExrateApp

        f = tmp_path / "a.xlsx"
        f.write_bytes(b"\0" * 1024)

        order = []
        app = SimpleNamespace(_batch_running=False)
        app._preflight_warn = lambda files: order.append(("preflight", list(files)))
        app._set_queue = lambda files: order.append(("set_queue", list(files)))

        event = SimpleNamespace(data=str(f))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "gui.app.parse_drop_data", lambda data, tk_root=None: [str(f)]
            )
            mp.setattr(
                "gui.app.resolve_excel_files",
                lambda paths, collect_rejected=False: ([str(f)], []),
            )
            BOTExrateApp._on_drop(app, event)

        assert order == [
            ("preflight", [str(f)]),
            ("set_queue", [str(f)]),
        ]

    def test_probe_error_does_not_block_selection(self, tmp_path):
        """A probe blowing up must never stop the file from being queued."""
        from gui.app import BOTExrateApp

        f = tmp_path / "a.xlsx"
        f.write_bytes(b"\0" * 1024)

        app = self._app()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "core.engine.LedgerEngine.preflight_file",
                MagicMock(side_effect=RuntimeError("boom")),
            )
            warn = MagicMock()
            mp.setattr("gui.app.messagebox.showwarning", warn)
            # Must not raise.
            BOTExrateApp._preflight_warn(app, [str(f)])

        warn.assert_not_called()
