#!/usr/bin/env python3
"""
gui/panels/_base_panel.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Shared SafePanel mixin.
---------------------------------------------------------------------------
Kills the duplicated `_safe_after` / `_destroyed` boilerplate that every
panel re-implemented. Used as a mixin so it can sit in front of either a
`ctk.CTkFrame` widget (CSVPanel, VersionPanel, RateTicker) or a plain
controller object that delegates scheduling to another widget (UpdateManager
schedules via `self.app.after`).

The `after()` target is resolved through `_after_target()`, which defaults to
`self`. Non-widget subclasses override it to return the widget they own.
"""

import contextlib
import logging
import tkinter

logger = logging.getLogger(__name__)


class SafePanel:
    """Mixin providing post-destroy-safe Tk scheduling.

    Provides:
      - `self._destroyed` flag, initialised to False in `__init__`.
      - `destroy()` override that flips the flag and cancels every still-
        pending `after()` callback before real teardown, so a queued timer
        can never fire against a torn-down panel.
      - `_safe_after(ms, func, *args)` that no-ops once destroyed and swallows
        the RuntimeError / TclError raised when callbacks land after teardown.

    Mix in BEFORE the Tk base class, e.g. `class Foo(SafePanel, ctk.CTkFrame)`.
    """

    # Class-level default so controller subclasses that skip
    # SafePanel.__init__ (e.g. UpdateManager initialises its own state and
    # never chains up) still work — the instance list is created lazily.
    _pending_after_ids = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._destroyed = False
        # after() ids still waiting to fire; cancelled in destroy(). Fired
        # callbacks remove their own id so the list stays bounded on panels
        # that reschedule indefinitely (e.g. the rate ticker).
        self._pending_after_ids: list[str] = []

    def _after_ids(self) -> list:
        """Per-instance pending-id list, created on first use."""
        if self._pending_after_ids is None:
            self._pending_after_ids = []
        return self._pending_after_ids

    def _after_target(self):
        """Widget on which `after()` is scheduled. Defaults to self."""
        return self

    def destroy(self):
        """Mark as destroyed and cancel pending callbacks before teardown."""
        self._destroyed = True
        self._cancel_pending_afters()
        super().destroy()

    def _cancel_pending_afters(self):
        """after_cancel every still-pending `_safe_after` callback.

        Already-fired or otherwise unknown ids make Tk raise TclError from
        `after_cancel` — suppressed, since the goal (callback will not run)
        is already met. RuntimeError covers interpreter-teardown races.
        """
        pending = self._after_ids()
        self._pending_after_ids = []
        if not pending:
            return
        try:
            target = self._after_target()
        except (RuntimeError, AttributeError):
            return
        for after_id in pending:
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                target.after_cancel(after_id)

    def _safe_after(self, ms, func, *args):
        """Thread-safe after() — ignores callbacks scheduled post-destroy.

        TclError ("application has been destroyed") is NOT a RuntimeError
        subclass, so both must be caught to keep worker threads alive.
        The returned after-id is tracked so `destroy()` can cancel it; the
        wrapper drops its own id once fired to keep the tracking bounded.
        """
        if self._destroyed:
            return
        id_box: list[str] = []

        def _invoke():
            if id_box:
                with contextlib.suppress(ValueError):
                    self._after_ids().remove(id_box[0])
            func(*args)

        try:
            after_id = self._after_target().after(ms, _invoke)
        except (RuntimeError, tkinter.TclError):
            self._destroyed = True
            return
        id_box.append(after_id)
        self._after_ids().append(after_id)
