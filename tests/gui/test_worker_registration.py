#!/usr/bin/env python3
"""
tests/gui/test_worker_registration.py
---------------------------------------------------------------------------
Source-scan guardrail: every threading.Thread( spawn site in gui/ must either
register with the ThreadRegistry (so _on_app_close can account for it before
self.destroy()) or appear in the documented EXEMPT table below.

The scan ast-parses every gui/**/*.py file, finds each Thread( call, and
checks that the ENCLOSING function also references the registry
(`_register_worker(` or `.register(`). The EXEMPT table pins today's known
spawn sites that do not register in-function — so this test fails the moment
a NEW unregistered Thread( spawn lands in gui/, while not blocking the
already-audited ones.

Pure source analysis: no display needed, runs on headless CI.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = REPO_ROOT / "gui"

# A spawn site "registers" when its enclosing function references the app's
# _register_worker helper or calls .register( on a registry object.
# (`.register(` does NOT match `.unregister(` — the dot must immediately
# precede `register(`.)
_REGISTER_RE = re.compile(r"_register_worker\(|\.register\(")

# (path relative to repo root, enclosing function name) -> documented reason.
# Keyed by function name (not line number) so unrelated edits do not churn
# this table. Every entry must correspond to a real unregistered spawn site
# (enforced by test_exempt_table_is_not_stale).
EXEMPT: dict[tuple[str, str], str] = {
    ("gui/panels/rate_ticker.py", "start"): (
        "Registered EXTERNALLY: gui/app.py registers rate_ticker._worker "
        "with thread_registry right after RateTicker.start() — the spawning "
        "method itself never sees the registry."
    ),
    ("gui/panels/tray_manager.py", "_start_tray"): (
        "pystray icon event loop; owned by TrayManager and stopped via "
        "tray.cleanup() in _on_app_close step 5, not the thread registry."
    ),
    ("gui/panels/csv_panel.py", "_on_import_csv"): (
        "Short-lived CSV import daemon; UI re-entry blocked by "
        "_set_buttons_enabled, teardown-safe via SafePanel._safe_after."
    ),
    ("gui/panels/csv_panel.py", "_on_export_csv"): (
        "Short-lived CSV export daemon; UI re-entry blocked by "
        "_set_buttons_enabled, teardown-safe via SafePanel._safe_after."
    ),
    ("gui/panels/token_dialog.py", "_on_test_keys"): (
        "Fire-and-forget key-test daemon; marshals back through the "
        "dialog's destroy-guarded after()."
    ),
    ("gui/panels/update_banner.py", "check_for_updates"): (
        "Startup update-check daemon; results marshalled via the banner's "
        "destroy-guarded callbacks."
    ),
    ("gui/panels/update_banner.py", "_do_download"): (
        "Update download daemon; auto_updater carries its own destroyed "
        "flag (mark_destroyed in _on_app_close step 4)."
    ),
    ("gui/panels/version_panel.py", "_on_ping_api"): (
        "Fire-and-forget API ping daemon behind SafePanel._safe_after."
    ),
    ("gui/panels/version_panel.py", "_on_check_update"): (
        "Fire-and-forget update-check daemon behind SafePanel._safe_after."
    ),
    ("gui/panels/version_panel.py", "_on_browse_versions"): (
        "Fire-and-forget release-list daemon behind SafePanel._safe_after."
    ),
    ("gui/panels/version_panel.py", "_download_in_app"): (
        "Update download daemon; auto_updater carries its own destroyed "
        "flag (mark_destroyed in _on_app_close step 4)."
    ),
}


def _iter_thread_spawn_sites():
    """Yield (relpath, enclosing function, lineno, registers) per Thread(."""
    sites = []
    for path in sorted(GUI_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name != "Thread":
                continue
            enclosing = node
            while enclosing in parents and not isinstance(
                enclosing, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                enclosing = parents[enclosing]
            if isinstance(enclosing, ast.FunctionDef | ast.AsyncFunctionDef):
                func_name = enclosing.name
                segment = ast.get_source_segment(source, enclosing) or ""
            else:  # module-level spawn: scan the whole file
                func_name = "<module>"
                segment = source
            sites.append(
                (rel, func_name, node.lineno, bool(_REGISTER_RE.search(segment)))
            )
    return sites


_SITES = _iter_thread_spawn_sites()
_IDS = [f"{rel}::{func}::L{lineno}" for rel, func, lineno, _ in _SITES]


def test_scan_found_the_known_spawn_surface():
    """The scanner itself must keep seeing the GUI's thread spawns — an empty
    scan would mean the guardrail silently stopped guarding."""
    assert len(_SITES) >= 10, _SITES
    rels = {rel for rel, _f, _l, _r in _SITES}
    assert "gui/handlers.py" in rels
    assert "gui/panels/rate_audit_dialog.py" in rels


@pytest.mark.parametrize(
    ("rel", "func", "lineno", "registers"), _SITES, ids=_IDS
)
def test_every_gui_thread_spawn_registers_or_is_exempt(
    rel, func, lineno, registers
):
    if registers:
        return
    assert (rel, func) in EXEMPT, (
        f"NEW unregistered thread spawn: {rel}:{lineno} in {func}(). "
        f"Either register the worker (app._register_worker / "
        f"thread_registry.register) so _on_app_close can account for it, "
        f"or add a documented entry to EXEMPT in this test."
    )


def test_exempt_table_is_not_stale():
    """Every EXEMPT entry must still match a real unregistered spawn site —
    when a spawn gets registered (or removed/renamed), drop its entry so the
    table never shadows a future regression."""
    unregistered = {
        (rel, func) for rel, func, _lineno, registers in _SITES
        if not registers
    }
    stale = set(EXEMPT) - unregistered
    assert not stale, f"EXEMPT entries no longer needed: {sorted(stale)}"
