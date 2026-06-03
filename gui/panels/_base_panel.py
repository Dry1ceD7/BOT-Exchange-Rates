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

import logging
import tkinter

logger = logging.getLogger(__name__)


class SafePanel:
    """Mixin providing post-destroy-safe Tk scheduling.

    Provides:
      - `self._destroyed` flag, initialised to False in `__init__`.
      - `destroy()` override that flips the flag before real teardown.
      - `_safe_after(ms, func, *args)` that no-ops once destroyed and swallows
        the RuntimeError / TclError raised when callbacks land after teardown.

    Mix in BEFORE the Tk base class, e.g. `class Foo(SafePanel, ctk.CTkFrame)`.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._destroyed = False

    def _after_target(self):
        """Widget on which `after()` is scheduled. Defaults to self."""
        return self

    def destroy(self):
        """Mark as destroyed before actual teardown."""
        self._destroyed = True
        super().destroy()

    def _safe_after(self, ms, func, *args):
        """Thread-safe after() — ignores callbacks scheduled post-destroy.

        TclError ("application has been destroyed") is NOT a RuntimeError
        subclass, so both must be caught to keep worker threads alive.
        """
        if self._destroyed:
            return
        try:
            self._after_target().after(ms, func, *args)
        except (RuntimeError, tkinter.TclError):
            self._destroyed = True
