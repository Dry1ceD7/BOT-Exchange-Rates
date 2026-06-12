#!/usr/bin/env python3
"""
core/config_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Persistent Settings Manager
---------------------------------------------------------------------------
JSON-backed configuration file for user preferences (appearance, auto-update,
custom directories). Gracefully handles missing or corrupt config files.

v3.2.2: In-memory caching to avoid re-reading disk on every get() call.
"""

import json
import logging
import math
import tempfile
import threading
from pathlib import Path
from typing import Any

from core.constants import (
    API_TIMEOUT_SECONDS,
    MAX_API_TIMEOUT_SECONDS,
    MIN_API_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    "appearance": "system",
    # v3.3.0: UI language ('en' default, 'th' Thai). Read by core/i18n.py.
    #          Only on-screen text is translated; logs/audit CSV stay English.
    "language": "en",
    "auto_update": True,
    # Single source of truth for the default API read timeout is
    # core.constants.API_TIMEOUT_SECONDS (BOTClient falls back to the same
    # constant when this key is missing or invalid).
    "api_timeout_seconds": API_TIMEOUT_SECONDS,
    # Rate type for ledger formula injection. USD/EUR (and extra currencies)
    # support buying_transfer ("Buying TT") and selling only — those are the
    # rates fetched/stored and the only ExRate columns that exist.
    "rate_type": "buying_transfer",
    # v3.1.0: Anomaly guardian threshold (±percentage)
    "anomaly_threshold_pct": 5.0,
    # v3.1.0: Scheduled auto-processing
    "scheduler_enabled": False,
    "scheduler_time": "23:00",
    "scheduler_paths": [],
    # Skip a scheduled run when the trigger day is a weekend / BOT holiday.
    # Off by default — existing installs keep running every day unchanged.
    "scheduler_skip_weekends": False,
    "scheduler_skip_holidays": False,
    # NOTE: "scheduler_last_run" is deliberately NOT a default here — it is
    # machine-local run state (written by the scheduler after each run) and
    # must never be seeded, exported, or imported across PCs.
}

SETTINGS_FILENAME = "settings.json"

# Keys that must NEVER be written to an exported settings file or accepted from
# an imported one. Secrets live in the OS keyring (core/secure_tokens.py), not
# here — but this denylist is a belt-and-suspenders guard so that if a token-ish
# key is ever added to the settings dict by mistake it can never leak through the
# multi-PC export/import path. Matching is case-insensitive substring on the key.
_SENSITIVE_KEY_MARKERS = ("token", "secret", "password", "passwd", "apikey", "api_key", "key")

# Upper bound for the anomaly guardian threshold (%). Shared with the GUI
# validator (gui/panels/settings_modal.py) so an imported settings file cannot
# smuggle in a value the modal itself would reject.
MAX_ANOMALY_THRESHOLD_PCT = 1000.0


class SettingsManager:
    """Load, save, and manage persistent user settings with in-memory cache."""

    def __init__(self, config_dir: str | None = None):
        if config_dir is None:
            from core.paths import get_project_root
            project_root = get_project_root()
            config_dir = str(Path(project_root) / "data")
        self._config_dir = config_dir
        # Keep _filepath as str: consumed by open()/json and os.replace below.
        self._filepath = str(Path(config_dir) / SETTINGS_FILENAME)
        self._cache: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        """Load settings from disk (cached after first read). Returns defaults on any error."""
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
        return self._load_from_disk()

    def _load_from_disk(self, force: bool = False) -> dict[str, Any]:
        """Read settings from disk and update cache.

        Args:
            force: When True, bypasses cache and always reads from disk.
        """
        with self._lock:
            if not force and self._cache is not None:
                return dict(self._cache)
            merged = self._read_disk_locked()
            self._cache = merged
            return dict(merged)

    def _read_disk_locked(self) -> dict[str, Any]:
        """Read settings.json and merge with defaults. Caller holds the lock.

        Never consults the in-memory cache: used by paths that must see the
        latest on-disk state (set/import merge bases), because multiple
        long-lived SettingsManager instances coexist (GUI, scheduler, engine,
        API client, i18n) and a per-instance cache can be stale.
        Returns defaults on any error.
        """
        if not Path(self._filepath).exists():
            return dict(DEFAULT_SETTINGS)
        try:
            with Path(self._filepath).open(encoding="utf-8") as f:
                data = json.load(f)
            # Shape guard (mirrors import_settings): valid JSON that is not
            # an object must not reach dict.update — a scalar string raises
            # ValueError (crashing every load) and a pair-like array (e.g.
            # ["ab"]) would silently merge as junk keys ({"a": "b"}).
            if not isinstance(data, dict):
                logger.warning(
                    "Settings file does not contain a JSON object (got %s). "
                    "Using defaults.",
                    type(data).__name__,
                )
                return dict(DEFAULT_SETTINGS)
            # Merge with defaults to fill any missing keys
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except (ValueError, OSError, TypeError) as e:
            # ValueError subsumes json.JSONDecodeError (its subclass).
            logger.warning(
                "Settings file corrupt or unreadable (%s). Using defaults.",
                e,
            )
            return dict(DEFAULT_SETTINGS)

    def reload(self) -> dict[str, Any]:
        """Force re-read from disk, bypassing cache."""
        with self._lock:
            self._cache = None
        return self._load_from_disk(force=True)

    def save(self, settings: dict[str, Any]) -> None:
        """Persist settings to disk and update cache."""
        with self._lock:
            self._save_locked(settings)

    def _save_locked(self, settings: dict[str, Any]) -> None:
        """Persist settings to disk and update cache. Caller holds the lock.

        Deliberately NOT ``core.workbook_io.atomic_write_text``: several
        long-lived SettingsManager instances (GUI, scheduler, engine, API
        client, i18n) can save concurrently, so the unique temp name from
        ``tempfile.NamedTemporaryFile`` is load-bearing — the shared helper's
        fixed ``.tmp~`` sibling would let two savers race on one temp path.
        """
        Path(self._config_dir).mkdir(parents=True, exist_ok=True)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._config_dir,
                prefix="settings.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(merged, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            # Path.replace is os.replace under the hood — atomic same-FS rename.
            Path(tmp_path).replace(self._filepath)
        except OSError:
            try:
                if tmp_path and Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            except OSError:
                pass
            raise
        finally:
            try:
                if tmp_path and Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            except OSError:
                pass
        self._cache = merged

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting value (from cache)."""
        return self.load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a single setting value and persist.

        Holds the lock across the full load→modify→save cycle so two
        concurrent set() calls cannot clobber each other (the scheduler
        runs on a background thread, so concurrent sets are real).

        The merge base is re-read from DISK, never this instance's cache:
        several long-lived SettingsManager instances coexist (GUI app,
        scheduler panel, settings modal, engine, API client, i18n), so a
        stale per-instance cache would silently revert keys persisted by a
        sibling instance (lost update). _save_locked refreshes the cache.
        """
        with self._lock:
            settings = self._read_disk_locked()
            settings[key] = value
            self._save_locked(settings)

    # ------------------------------------------------------------------ #
    #  Multi-PC deployment: export / import the settings JSON
    # ------------------------------------------------------------------ #
    @staticmethod
    def _strip_sensitive(settings: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``settings`` with any sensitive-looking key removed.

        Secrets are stored in the OS keyring, not in settings.json, so this is a
        defensive guard: an admin can copy an exported file to another PC without
        any chance of leaking a token even if one were ever (mistakenly) parked
        in the settings dict.
        """
        return {
            k: v
            for k, v in settings.items()
            if not any(m in k.lower() for m in _SENSITIVE_KEY_MARKERS)
        }

    @staticmethod
    def _coerce_imported(accepted: dict[str, Any]) -> dict[str, Any]:
        """Coerce imported values to the types declared in DEFAULT_SETTINGS.

        A hand-edited or foreign settings file may carry e.g. "5.0" (string)
        for the anomaly threshold; without coercion that string propagates
        into the anomaly guard and aborts a batch with an uncaught TypeError.
        Numeric keys are coerced via float() (int defaults round-trip through
        int()); booleans accept real bools or common string spellings.
        Uncoercible or out-of-range values fall back to the default with a
        warning naming the key — an import must never poison a batch run.
        """
        coerced: dict[str, Any] = {}
        for key, value in accepted.items():
            default = DEFAULT_SETTINGS[key]
            # bool first: bool is a subclass of int, so the numeric branch
            # below would otherwise swallow it.
            if isinstance(default, bool):
                if isinstance(value, bool):
                    coerced[key] = value
                    continue
                text = str(value).strip().lower()
                if text in ("true", "1", "yes", "on"):
                    coerced[key] = True
                    continue
                if text in ("false", "0", "no", "off"):
                    coerced[key] = False
                    continue
            elif isinstance(default, (int, float)):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    number = None
                if number is not None and math.isfinite(number) and number > 0:
                    if (
                        key == "anomaly_threshold_pct"
                        and number > MAX_ANOMALY_THRESHOLD_PCT
                    ):
                        pass  # out of range — fall through to the default
                    elif key == "api_timeout_seconds" and not (
                        MIN_API_TIMEOUT_SECONDS
                        <= number
                        <= MAX_API_TIMEOUT_SECONDS
                    ):
                        # Out of range — fall through to the default. A
                        # pathological timeout (0.001) would make EVERY BOT
                        # call time out, with tenacity retrying each chunk
                        # 4 times; api_client._resolve_timeout_seconds
                        # enforces the same bounds for hand-edited files.
                        pass
                    else:
                        coerced[key] = (
                            int(number) if isinstance(default, int) else number
                        )
                        continue
            elif isinstance(value, type(default)):
                # str / list keys: accept only the matching container type.
                coerced[key] = value
                continue
            logger.warning(
                "Imported setting %r has invalid value %r; using default %r.",
                key,
                value,
                default,
            )
            coerced[key] = default
        return coerced

    def export_settings(self, dest_path: str) -> str:
        """Write the current settings (minus secrets) to ``dest_path`` as JSON.

        The exported file is a portable, human-readable snapshot intended for
        copying to another PC. Returns the destination path on success. Raises
        OSError on a write failure (callers humanize it for the user).
        """
        # load() returns a defaults-merged copy, so every known key is present
        # and the file is self-describing on the target machine.
        exportable = self._strip_sensitive(self.load())
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as f:
            json.dump(exportable, f, indent=2, ensure_ascii=False)
        return str(dest)

    def import_settings(self, src_path: str) -> dict[str, Any]:
        """Load settings from ``src_path``, merge them in, persist, and return.

        Only keys recognised in DEFAULT_SETTINGS are accepted (unknown keys are
        dropped so a hand-edited or foreign file can't inject junk), any
        sensitive-looking key is stripped, and values are type-coerced against
        DEFAULT_SETTINGS (uncoercible values fall back to the default with a
        warning). The merged result is persisted via the normal locked save
        path and the in-memory cache is refreshed.

        Raises ValueError if the file is not valid JSON or not a JSON object, and
        OSError if it cannot be read (callers humanize these for the user).
        """
        with Path(src_path).open(encoding="utf-8") as f:
            try:
                incoming = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Not a valid settings file: {e}") from e
        if not isinstance(incoming, dict):
            raise ValueError("Settings file must contain a JSON object.")
        # Accept only known keys, strip any sensitive ones, then coerce types.
        accepted = self._coerce_imported(
            self._strip_sensitive(
                {k: v for k, v in incoming.items() if k in DEFAULT_SETTINGS}
            )
        )
        with self._lock:
            # Merge over the latest on-disk state (not this instance's cache)
            # so the import can't revert keys persisted by a sibling instance.
            base = self._read_disk_locked()
            base.update(accepted)
            self._save_locked(base)
            return dict(self._cache) if self._cache is not None else base
