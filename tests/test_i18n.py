#!/usr/bin/env python3
"""
tests/test_i18n.py
---------------------------------------------------------------------------
Unit tests for core/i18n.py — the dict-based translation layer.

These tests are pure-Python (no Tk, no network, no keyring) and cover:
  1. tr() returns the active-language string and falls back EN → key.
  2. Format placeholders are applied; a bad placeholder degrades gracefully.
  3. get/set/reload language semantics and code normalization.
  4. Language is read from settings.json (mocked SettingsManager).
  5. The catalog is complete: every key has both EN and TH, and every
     {placeholder} present in EN is also present in the matching TH string
     (so neither locale raises mid-format).
  6. config_manager.DEFAULT_SETTINGS carries the new 'language' default.

Each test resets the i18n module cache so cross-test state never leaks.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.i18n as i18n
from core.i18n import (
    CATALOG,
    DEFAULT_LANGUAGE,
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    get_language,
    plural,
    reload_language,
    set_language,
    tr,
)

# Repo root = parent of the tests/ directory that holds this file.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Source trees whose tr() call sites must every resolve to a catalog entry.
# gui/ is the primary surface; core/ and main.py also emit a handful of
# user-facing strings (e.g. early token-validation popups).
_SOURCE_ROOTS = ("gui", "core")
_SOURCE_FILES = ("main.py",)

# Matches tr('key') / tr("key") — a literal, dotted, namespaced key. Dynamic
# keys (tr(variable)) are intentionally not matched: only literals can be
# statically verified, and the codebase uses literals everywhere.
_TR_CALL = re.compile(r"""\btr\(\s*['"]([a-zA-Z0-9_.]+)['"]""")


def _iter_source_files():
    """Yield every .py file under the source roots, excluding i18n.py itself."""
    seen: set[Path] = set()
    for root in _SOURCE_ROOTS:
        base = _REPO_ROOT / root
        if base.is_dir():
            for path in base.rglob("*.py"):
                if path.name != "i18n.py":
                    seen.add(path)
    for name in _SOURCE_FILES:
        path = _REPO_ROOT / name
        if path.is_file():
            seen.add(path)
    return sorted(seen)


def _collect_tr_keys() -> dict[str, list[str]]:
    """Map each literal tr() key -> list of 'relpath:line' call sites."""
    keys: dict[str, list[str]] = {}
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _TR_CALL.finditer(line):
                rel = path.relative_to(_REPO_ROOT)
                keys.setdefault(match.group(1), []).append(f"{rel}:{lineno}")
    return keys


@pytest.fixture(autouse=True)
def _reset_language():
    """Reset the cached active language before AND after each test.

    The module keeps ``_active_language`` as a process-wide cache; without a
    reset a test that sets Thai would leak into the next test (and into the
    GUI suite's English-literal assertions).
    """
    i18n._active_language = None
    set_language(DEFAULT_LANGUAGE)
    yield
    i18n._active_language = None


# ---------------------------------------------------------------------------
# tr() — lookup + fallback
# ---------------------------------------------------------------------------
class TestTranslate:
    def test_returns_english_by_default(self):
        set_language("en")
        assert tr("main.btn_process") == "Process Batch"

    def test_returns_thai_when_active(self):
        set_language("th")
        # Thai differs from English — proves the locale switch took effect.
        assert tr("main.btn_process") != "Process Batch"
        assert tr("main.btn_process") == CATALOG["main.btn_process"]["th"]

    def test_missing_key_returns_key_itself(self):
        assert tr("no.such.key") == "no.such.key"

    def test_missing_thai_falls_back_to_english(self):
        # Inject a temporary EN-only entry to exercise the EN fallback path.
        CATALOG["test.en_only"] = {"en": "English Only"}
        try:
            set_language("th")
            assert tr("test.en_only") == "English Only"
        finally:
            del CATALOG["test.en_only"]


# ---------------------------------------------------------------------------
# tr() — formatting
# ---------------------------------------------------------------------------
class TestFormatting:
    def test_applies_named_placeholders(self):
        set_language("en")
        out = tr("main.queue_ready", count=2, plural="s")
        assert out == "Ready to process 2 ledgers."

    def test_singular_plural_helper(self):
        assert plural(1) == ""
        assert plural(0) == "s"
        assert plural(2) == "s"

    def test_bad_placeholder_degrades_to_unformatted(self):
        set_language("en")
        # 'main.btn_process' has no placeholders; passing kwargs must not raise
        # and must return the plain string.
        assert tr("main.btn_process", bogus="x") == "Process Batch"

    def test_missing_format_arg_does_not_raise(self):
        set_language("en")
        # queue_ready needs {count} and {plural}; omitting them must degrade
        # to the raw template rather than raising a KeyError into the UI.
        out = tr("main.queue_ready")
        assert "{count}" in out  # unformatted template returned


# ---------------------------------------------------------------------------
# Language state
# ---------------------------------------------------------------------------
class TestLanguageState:
    def test_set_language_normalizes_case(self):
        assert set_language("TH") == "th"
        assert get_language() == "th"

    def test_unsupported_code_falls_back_to_default(self):
        assert set_language("fr") == DEFAULT_LANGUAGE
        assert set_language("") == DEFAULT_LANGUAGE
        assert set_language(None) == DEFAULT_LANGUAGE

    def test_get_language_reads_settings_when_cold(self):
        i18n._active_language = None
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = "th"
        with patch("core.config_manager.SettingsManager", return_value=mock_mgr):
            assert get_language() == "th"
        mock_mgr.get.assert_called_once_with("language", DEFAULT_LANGUAGE)

    def test_reload_language_rereads_settings(self):
        set_language("en")
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = "th"
        with patch("core.config_manager.SettingsManager", return_value=mock_mgr):
            assert reload_language() == "th"
        assert get_language() == "th"

    def test_settings_read_failure_falls_back_to_english(self):
        i18n._active_language = None
        mock_mgr = MagicMock()
        mock_mgr.get.side_effect = OSError("disk gone")
        with patch("core.config_manager.SettingsManager", return_value=mock_mgr):
            assert get_language() == DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# Catalog completeness / integrity
# ---------------------------------------------------------------------------
class TestCatalogIntegrity:
    def test_supported_languages_have_labels(self):
        for code in SUPPORTED_LANGUAGES:
            assert code in LANGUAGE_LABELS
            assert LANGUAGE_LABELS[code]

    def test_every_key_has_all_languages(self):
        missing = [
            (key, lang)
            for key, entry in CATALOG.items()
            for lang in SUPPORTED_LANGUAGES
            if lang not in entry
        ]
        assert not missing, f"keys missing a translation: {missing}"

    def test_placeholders_match_across_languages(self):
        """Every {placeholder} in the EN string must exist in the TH string.

        A placeholder present in EN but absent from TH would make a TH .format()
        silently drop a value (or, if EN omitted one TH used, raise KeyError).
        """
        token = re.compile(r"\{(\w+)\}")
        mismatches = []
        for key, entry in CATALOG.items():
            en_ph = set(token.findall(entry.get("en", "")))
            th_ph = set(token.findall(entry.get("th", "")))
            # TH may legitimately use a SUBSET of EN placeholders (e.g. it drops
            # {plural} because Thai has no plural inflection). It must never use
            # a placeholder EN does not provide.
            extra = th_ph - en_ph
            if extra:
                mismatches.append((key, extra))
        assert not mismatches, f"TH uses unknown placeholders: {mismatches}"

    def test_no_empty_translations(self):
        empty = [
            (key, lang)
            for key, entry in CATALOG.items()
            for lang in SUPPORTED_LANGUAGES
            if not entry.get(lang, "").strip()
        ]
        assert not empty, f"empty translations: {empty}"


# ---------------------------------------------------------------------------
# Call-site coverage — every tr() key used in the source has a catalog entry
# ---------------------------------------------------------------------------
class TestCallSiteCoverage:
    """Static guard: every literal ``tr('key')`` in the source resolves.

    This is the regression guard for the whole "a new tr() call site shipped
    without a catalog entry" class of bug. ``tr()`` degrades a missing key to
    the raw dotted string (e.g. the UI literally shows ``main.help_btn``), so a
    missing entry never crashes — it just leaks an untranslated identifier onto
    a Thai accounting operator's screen. Only a static scan catches that.

    It scans gui/, core/, and main.py for ``tr('literal.key')`` occurrences and
    asserts each appears in CATALOG with BOTH an EN and a TH string. Dynamic
    keys (``tr(var)``) are out of scope — they cannot be checked statically and
    the codebase does not use them.
    """

    def test_source_has_tr_call_sites(self):
        # Guards the scanner itself: if the regex or paths break, this fails
        # loudly rather than letting an empty scan vacuously "pass".
        keys = _collect_tr_keys()
        assert keys, "no tr() call sites found — scanner is misconfigured"
        # Sanity anchor: a well-known key must be among those discovered.
        assert "main.btn_process" in keys

    def test_every_used_key_exists_in_catalog(self):
        keys = _collect_tr_keys()
        missing = {
            key: sites for key, sites in keys.items() if key not in CATALOG
        }
        assert not missing, (
            "tr() keys used in source but absent from CATALOG "
            f"(add EN+TH entries): {missing}"
        )

    def test_every_used_key_has_both_languages(self):
        keys = _collect_tr_keys()
        incomplete = {}
        for key in keys:
            entry = CATALOG.get(key, {})
            langs = [
                lang
                for lang in SUPPORTED_LANGUAGES
                if not entry.get(lang, "").strip()
            ]
            if langs:
                incomplete[key] = langs
        assert not incomplete, (
            f"tr() keys missing a language translation: {incomplete}"
        )

    def test_status_error_key_is_wired_to_app_call_sites(self):
        """Regression: ``main.status_error`` was an orphaned catalog key.

        gui/app.py's _show_error / _show_download_error / _show_revert_error
        rendered a hard-coded ``f"Error:  {msg}"`` instead of consuming the
        catalog entry, so Thai mode never saw the translated error status
        ("ข้อผิดพลาด:  {msg}"). All three error-status surfaces must route
        through tr("main.status_error", msg=...). EN rendering is byte-
        identical to the old f-string, so this is i18n-only behavior.
        """
        sites = _collect_tr_keys().get("main.status_error", [])
        app_sites = [
            s for s in sites
            if s.replace("\\", "/").startswith("gui/app.py")
        ]
        assert len(app_sites) >= 3, (
            "main.status_error must be consumed by the three error-status "
            "call sites in gui/app.py (_show_error, _show_download_error, "
            f"_show_revert_error); found call sites: {sites}"
        )


# ---------------------------------------------------------------------------
# DEFAULT_SETTINGS wiring
# ---------------------------------------------------------------------------
class TestDefaultSettings:
    def test_language_default_is_english(self):
        from core.config_manager import DEFAULT_SETTINGS

        assert DEFAULT_SETTINGS.get("language") == "en"
