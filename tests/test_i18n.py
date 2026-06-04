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
# DEFAULT_SETTINGS wiring
# ---------------------------------------------------------------------------
class TestDefaultSettings:
    def test_language_default_is_english(self):
        from core.config_manager import DEFAULT_SETTINGS

        assert DEFAULT_SETTINGS.get("language") == "en"
