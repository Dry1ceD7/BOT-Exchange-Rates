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
        btn_backups=_btn(),
        btn_export_exrate=_btn(),
        btn_reveal=_btn(),
        lbl_status=MagicMock(),
        lbl_queue=MagicMock(),
        dz_text=MagicMock(),
        dz_sub=MagicMock(),
        lbl_empty_state=MagicMock(),
        progressbar=MagicMock(),
        batch_handler=MagicMock(),
        update_idletasks=MagicMock(),
        # No backup_mgr / no real Tk title — _refresh_revert_state fails OPEN and
        # _set_window_title no-ops (guarded getattr), so the lock/unlock helpers
        # exercise their full control flow on the stand-in without a Tk root.
    )
    # Bind the real busy-status / unlock helpers so guarded methods that call
    # back into them exercise the genuine control flow on the stand-in.
    app._flash_busy_status = lambda: BOTExrateApp._flash_busy_status(app)
    app._unlock_ui_after_batch = lambda: BOTExrateApp._unlock_ui_after_batch(app)
    app._lock_ui_for_batch = lambda: BOTExrateApp._lock_ui_for_batch(app)
    app._settle_progressbar = lambda value: BOTExrateApp._settle_progressbar(app, value)
    app._set_window_title = lambda suffix=None: BOTExrateApp._set_window_title(app, suffix)
    app._refresh_revert_state = lambda: BOTExrateApp._refresh_revert_state(app)
    app._show_empty_state = lambda: BOTExrateApp._show_empty_state(app)
    app._hide_empty_state = lambda: BOTExrateApp._hide_empty_state(app)
    app.btn_clear_queue = _btn()
    app._show_clear_queue = lambda: BOTExrateApp._show_clear_queue(app)
    app._hide_clear_queue = lambda: BOTExrateApp._hide_clear_queue(app)
    app._dedup_new = lambda c: BOTExrateApp._dedup_new(app, c)
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
        app = SimpleNamespace(_batch_running=False, file_queue=[])
        app._preflight_warn = lambda files: order.append(("preflight", list(files)))
        app._set_queue = lambda files: order.append(("set_queue", list(files)))
        app._dedup_new = lambda c: BOTExrateApp._dedup_new(app, c)
        # A pure FILE drop stays synchronous and routes through _finish_drop.
        app._finish_drop = (
            lambda excel, rejected: BOTExrateApp._finish_drop(app, excel, rejected)
        )

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


# ---------------------------------------------------------------------------
# app-polish #2 — drop/browse APPEND (dedup) instead of REPLACE; Clear queue
# ---------------------------------------------------------------------------

class TestAdditiveQueue:
    def test_dedup_new_excludes_already_queued(self):
        from gui.app import BOTExrateApp

        app = _make_app(file_queue=["/a/jan.xlsx", "/a/feb.xlsx"])
        # feb is a dup (normalized); mar is new.
        result = BOTExrateApp._dedup_new(
            app, ["/a/./feb.xlsx", "/a/mar.xlsx", "/a/mar.xlsx"],
        )
        assert result == ["/a/mar.xlsx"]

    def test_finish_drop_appends_to_existing_queue(self):
        from gui.app import BOTExrateApp

        app = _make_app(file_queue=["/a/jan.xlsx"])
        app._preflight_warn = MagicMock()
        captured = {}
        app._set_queue = lambda files: captured.__setitem__("files", list(files))

        BOTExrateApp._finish_drop(app, ["/a/feb.xlsx"], [])

        # The new file is APPENDED, not a replacement — both survive.
        assert captured["files"] == ["/a/jan.xlsx", "/a/feb.xlsx"]
        # Only the newly-added file is preflighted (no re-warn on jan).
        app._preflight_warn.assert_called_once_with(["/a/feb.xlsx"])

    def test_finish_drop_dedups_redrop(self):
        from gui.app import BOTExrateApp

        app = _make_app(file_queue=["/a/jan.xlsx"])
        app._preflight_warn = MagicMock()
        captured = {}
        app._set_queue = lambda files: captured.__setitem__("files", list(files))

        # Re-dropping the already-queued file must not duplicate it.
        BOTExrateApp._finish_drop(app, ["/a/jan.xlsx"], [])

        assert captured["files"] == ["/a/jan.xlsx"]
        # Nothing new -> no preflight warning round-trip.
        app._preflight_warn.assert_not_called()

    def test_clear_queue_resets_selection(self):
        from gui.app import BOTExrateApp

        app = _make_app(file_queue=["/a/jan.xlsx", "/a/feb.xlsx"])
        BOTExrateApp._on_clear_queue(app)

        assert app.file_queue == []
        app.btn_process.configure.assert_any_call(state="disabled")

    def test_clear_queue_ignored_mid_batch(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, file_queue=["/a/jan.xlsx"])
        BOTExrateApp._on_clear_queue(app)

        # A running batch owns the snapshot — Clear must not disturb file_queue.
        assert app.file_queue == ["/a/jan.xlsx"]
        assert (
            app.lbl_status.configure.call_args.kwargs["text"]
            == tr("main.status_busy")
        )


# ---------------------------------------------------------------------------
# app-polish #3 — progress names the file + shows how many REMAIN
# ---------------------------------------------------------------------------

class TestProgressRemaining:
    def test_progress_reports_remaining_and_names_file(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        BOTExrateApp._update_progress(app, idx=3, total=10, fname="mar.xlsx", error=None)

        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert status == tr(
            "main.status_progress_ok",
            idx=3, total=10, remaining=7, fname="mar.xlsx",
        )

    def test_progress_skip_reports_remaining(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        BOTExrateApp._update_progress(app, idx=10, total=10, fname="dec.xlsx", error="boom")

        status = app.lbl_status.configure.call_args.kwargs["text"]
        assert status == tr(
            "main.status_progress_skipped",
            idx=10, total=10, remaining=0, fname="dec.xlsx",
        )


# ---------------------------------------------------------------------------
# app-polish #6 — Revert/Backups greyed when there is nothing to revert
# ---------------------------------------------------------------------------

class TestRevertGreyedWhenEmpty:
    def _app(self, backup_dir):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            backup_mgr=SimpleNamespace(backup_dir=str(backup_dir)),
            btn_revert=_btn(),
            btn_backups=_btn(),
        )
        app._refresh_revert_state = lambda: BOTExrateApp._refresh_revert_state(app)
        return app

    def test_disabled_when_no_backups(self, tmp_path):
        app = self._app(tmp_path)  # empty backup dir
        app._refresh_revert_state()

        app.btn_revert.configure.assert_called_with(state="disabled")
        app.btn_backups.configure.assert_called_with(state="disabled")

    def test_enabled_when_a_backup_exists(self, tmp_path):
        (tmp_path / "ledger__bak__20260101_120000.xlsx").write_bytes(b"x")
        app = self._app(tmp_path)
        app._refresh_revert_state()

        app.btn_revert.configure.assert_called_with(state="normal")
        app.btn_backups.configure.assert_called_with(state="normal")

    def test_probe_error_fails_open(self):
        from gui.app import BOTExrateApp

        # A backup_mgr whose backup_dir is unusable -> probe raises -> fail OPEN.
        app = SimpleNamespace(
            backup_mgr=SimpleNamespace(backup_dir=None),
            btn_revert=_btn(),
            btn_backups=_btn(),
        )
        BOTExrateApp._refresh_revert_state(app)
        app.btn_revert.configure.assert_called_with(state="normal")


# ---------------------------------------------------------------------------
# app-polish #8 — folder drop resolves listing OFF the Tk thread
# ---------------------------------------------------------------------------

class TestFolderDropOffloaded:
    def test_folder_drop_uses_worker_thread(self, tmp_path, monkeypatch):
        """A dropped DIRECTORY must not call resolve_excel_files inline on the
        Tk thread; it spawns a worker and marshals the result via after()."""
        from core.workers.thread_registry import ThreadRegistry
        from gui.app import BOTExrateApp

        (tmp_path / "a.xlsx").write_bytes(b"\0" * 16)
        scheduled = []

        app = SimpleNamespace(
            _batch_running=False,
            _closing=False,
            lbl_status=MagicMock(),
            after=lambda ms, fn: scheduled.append((ms, fn)),
            thread_registry=ThreadRegistry(),
        )
        app._finish_drop = MagicMock()
        # Bind the real shutdown-safe marshal + registry helpers so the worker's
        # guarded marshal-back and thread registration are exercised (#1).
        app._safe_marshal = BOTExrateApp._safe_marshal.__get__(app)
        app._register_worker = BOTExrateApp._register_worker.__get__(app)

        # Fake Thread that captures the target instead of running it on a real
        # OS thread — keeps the test deterministic and leak-free.
        captured = {}

        class FakeThread:
            def __init__(self, target=None, daemon=None, **k):
                captured["target"] = target
                self.name = "FakeThread"

            def start(self):
                captured["started"] = True

            def is_alive(self):
                return False

        monkeypatch.setattr("gui.app.threading.Thread", FakeThread)
        monkeypatch.setattr(
            "gui.app.parse_drop_data", lambda data, tk_root=None: [str(tmp_path)],
        )

        event = SimpleNamespace(data=str(tmp_path))
        BOTExrateApp._on_drop(app, event)

        # A worker thread was spawned (listing offloaded), and _finish_drop was
        # NOT called inline on the Tk thread.
        assert captured.get("started") is True
        app._finish_drop.assert_not_called()
        # The worker is registered so _on_app_close.shutdown_all accounts for it
        # before self.destroy() (#1).
        assert "FolderResolveWorker" in app.thread_registry.status()
        # The status shows a scanning hint while the worker runs.
        assert app.lbl_status.configure.called

        # Run the captured worker target; it must marshal the result via after().
        captured["target"]()
        assert len(scheduled) == 1
        # Executing the marshalled callback delivers the resolved files.
        scheduled[0][1]()
        app._finish_drop.assert_called_once()
        excel_files = app._finish_drop.call_args.args[0]
        assert any(p.endswith("a.xlsx") for p in excel_files)

    def test_file_drop_stays_synchronous(self, tmp_path, monkeypatch):
        """A pure FILE drop must NOT spawn a thread (cheap, stays inline)."""
        from gui.app import BOTExrateApp

        f = tmp_path / "a.xlsx"
        f.write_bytes(b"\0" * 16)

        app = SimpleNamespace(_batch_running=False, lbl_status=MagicMock())
        app._finish_drop = MagicMock()

        spawned = {"n": 0}
        monkeypatch.setattr(
            "gui.app.threading.Thread",
            lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1) or MagicMock(),
        )
        monkeypatch.setattr(
            "gui.app.parse_drop_data", lambda data, tk_root=None: [str(f)],
        )

        BOTExrateApp._on_drop(app, SimpleNamespace(data=str(f)))

        assert spawned["n"] == 0
        app._finish_drop.assert_called_once()


# ---------------------------------------------------------------------------
# app-polish #11 — window title reflects the in-flight batch
# ---------------------------------------------------------------------------

class TestWindowTitleReflectsBatch:
    def _title_app(self):
        from gui.app import BOTExrateApp

        captured = {"titles": []}
        app = SimpleNamespace(
            _base_title="BOT Exchange Rate Processor  |  V9",
            title=lambda text: captured["titles"].append(text),
        )
        app._set_window_title = (
            lambda suffix=None: BOTExrateApp._set_window_title(app, suffix)
        )
        return app, captured

    def test_title_shows_progress_then_restores(self):
        from gui.app import BOTExrateApp

        app, captured = self._title_app()
        BOTExrateApp._set_window_title(app, "Processing 2 of 5")
        assert "Processing 2 of 5" in captured["titles"][-1]
        assert "BOT Exchange Rate Processor" in captured["titles"][-1]

        BOTExrateApp._set_window_title(app, None)
        # Restored to exactly the idle base title.
        assert captured["titles"][-1] == app._base_title

    def test_update_progress_sets_title(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True)
        titles = []
        app._set_window_title = lambda suffix=None: titles.append(suffix)

        BOTExrateApp._update_progress(app, idx=2, total=5, fname="x.xlsx", error=None)

        assert titles and titles[-1] == tr(
            "main.title_processing", idx=2, total=5,
        )


# ---------------------------------------------------------------------------
# app-polish #12 — empty-state guidance shown only when the queue is empty
# ---------------------------------------------------------------------------

class TestEmptyStateGuidance:
    def test_set_queue_hides_empty_state_and_shows_clear(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False)
        hidden = {"empty": False, "clear": False}
        app._hide_empty_state = lambda: hidden.__setitem__("empty", True)
        app._show_clear_queue = lambda: hidden.__setitem__("clear", True)

        BOTExrateApp._set_queue(app, ["a.xlsx"])

        assert hidden["empty"] is True
        assert hidden["clear"] is True

    def test_reset_shows_empty_state_and_hides_clear(self):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=False, file_queue=["a.xlsx"])
        shown = {"empty": False, "clear_hidden": False}
        app._show_empty_state = lambda: shown.__setitem__("empty", True)
        app._hide_clear_queue = lambda: shown.__setitem__("clear_hidden", True)

        BOTExrateApp._reset_queue_after_run(app)

        assert shown["empty"] is True
        assert shown["clear_hidden"] is True
        assert app.file_queue == []


# ---------------------------------------------------------------------------
# round-9 — crash-recovery: offer to resume an interrupted batch on launch
# ---------------------------------------------------------------------------

class TestResumeOnLaunch:
    """_offer_batch_resume reads the engine manifest and, when unfinished files
    remain, offers to re-queue ONLY those. Accepting loads them; declining drops
    the manifest so the prompt never reappears."""

    def _resume_app(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            _batch_running=False,
            file_queue=[],
            lbl_status=MagicMock(),
        )
        app._offer_batch_resume = (
            lambda: BOTExrateApp._offer_batch_resume(app)
        )
        return app

    def test_no_manifest_no_prompt(self, monkeypatch):
        # No pending files → never prompt, never touch the queue.
        fake_manifest = MagicMock()
        fake_manifest.pending_files.return_value = []
        monkeypatch.setattr(
            "core.engine.BatchManifest", lambda *a, **k: fake_manifest,
        )
        ask = MagicMock()
        monkeypatch.setattr("gui.app.messagebox.askyesno", ask)

        app = self._resume_app()
        app._offer_batch_resume()

        ask.assert_not_called()
        assert app.file_queue == []

    def test_accept_loads_only_unfinished_files(self, monkeypatch):
        fake_manifest = MagicMock()
        fake_manifest.pending_files.return_value = ["/a/feb.xlsx", "/a/mar.xlsx"]
        monkeypatch.setattr(
            "core.engine.BatchManifest", lambda *a, **k: fake_manifest,
        )
        monkeypatch.setattr(
            "gui.app.messagebox.askyesno", MagicMock(return_value=True),
        )

        app = self._resume_app()
        loaded = {}
        app._preflight_warn = MagicMock()
        app._set_queue = lambda files: loaded.__setitem__("files", list(files))

        app._offer_batch_resume()

        # Only the unfinished files are queued; the manifest is left in place
        # (the next real run rewrites it from the new selection).
        assert loaded["files"] == ["/a/feb.xlsx", "/a/mar.xlsx"]
        app._preflight_warn.assert_called_once_with(
            ["/a/feb.xlsx", "/a/mar.xlsx"]
        )
        fake_manifest.clear.assert_not_called()

    def test_decline_clears_manifest(self, monkeypatch):
        fake_manifest = MagicMock()
        fake_manifest.pending_files.return_value = ["/a/feb.xlsx"]
        monkeypatch.setattr(
            "core.engine.BatchManifest", lambda *a, **k: fake_manifest,
        )
        monkeypatch.setattr(
            "gui.app.messagebox.askyesno", MagicMock(return_value=False),
        )

        app = self._resume_app()
        app._set_queue = MagicMock()
        app._offer_batch_resume()

        # Declining drops the manifest so the prompt does not reappear, and the
        # queue is left empty.
        fake_manifest.clear.assert_called_once()
        app._set_queue.assert_not_called()

    def test_skipped_while_batch_running(self, monkeypatch):
        # A resume offer must never interrupt an in-flight batch.
        called = {"manifest": False}
        monkeypatch.setattr(
            "core.engine.BatchManifest",
            lambda *a, **k: called.__setitem__("manifest", True),
        )
        app = self._resume_app()
        app._batch_running = True
        app._offer_batch_resume()

        assert called["manifest"] is False
