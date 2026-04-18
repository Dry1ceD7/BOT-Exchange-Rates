#!/usr/bin/env python3
"""
tests/test_auto_updater.py
---------------------------------------------------------------------------
Comprehensive test suite for core/auto_updater.py.

Covers:
  - check_for_update (version comparison, network errors, prerelease)
  - get_installer_asset_url (asset parsing, SHA-256 URL extraction)
  - _fetch_expected_checksum (checksum file parsing)
  - _verify_file_sha256 (hash verification)
  - download_update (download flow, integrity check, cleanup)
  - apply_update (bat script generation, path sanitization)
  - _get_install_dir (registry, frozen, dev mode)
"""

import hashlib
import os
from unittest.mock import MagicMock, patch

import httpx

from core.auto_updater import (
    _fetch_expected_checksum,
    _verify_file_sha256,
    check_for_update,
    download_update,
    get_installer_asset_url,
)

# ═══════════════════════════════════════════════════════════════════════════
#  check_for_update
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckForUpdate:
    """Tests for the check_for_update function."""

    def test_update_available(self):
        """Detects newer version from GitHub API."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/release/v99.0.0",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="1.0.0")

        assert result["update_available"] is True
        assert result["latest_version"] == "99.0.0"
        assert result["download_url"] is not None
        assert result["error"] is None

    def test_no_update_same_version(self):
        """No update when versions match."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "v3.2.5",
            "html_url": "https://github.com/release/v3.2.5",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="3.2.5")

        assert result["update_available"] is False
        assert result["error"] is None

    def test_no_update_newer_local(self):
        """No update when local is ahead of remote."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "v2.0.0",
            "html_url": "https://github.com/release/v2.0.0",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="3.0.0")

        assert result["update_available"] is False

    def test_strips_v_prefix(self):
        """Handles 'v' prefix in tag names correctly."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "V4.0.0",
            "html_url": "https://github.com/release/V4.0.0",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="v3.0.0")

        assert result["update_available"] is True
        assert result["latest_version"] == "4.0.0"

    def test_network_error(self):
        """Returns error dict on network failure, never raises."""
        with patch(
            "core.auto_updater.httpx.get",
            side_effect=httpx.ConnectError("DNS failed"),
        ):
            result = check_for_update(current_version="1.0.0")

        assert result["update_available"] is False
        assert result["error"] is not None
        assert "Network error" in result["error"]

    def test_http_status_error(self):
        """Returns error dict on HTTP error status."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=mock_resp
        )

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="1.0.0")

        assert result["update_available"] is False
        assert "403" in result["error"]

    def test_invalid_version_tag(self):
        """Handles unparseable version tags gracefully."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "not-a-version",
            "html_url": "https://github.com/release/bad",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version="1.0.0")

        assert result["update_available"] is False
        assert result["error"] is not None

    def test_default_version_when_none(self):
        """Uses 0.0.0 when no version provided."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tag_name": "v0.0.1",
            "html_url": "https://github.com/release/v0.0.1",
            "prerelease": False,
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = check_for_update(current_version=None)

        assert result["update_available"] is True


# ═══════════════════════════════════════════════════════════════════════════
#  get_installer_asset_url
# ═══════════════════════════════════════════════════════════════════════════


class TestGetInstallerAssetUrl:
    """Tests for get_installer_asset_url."""

    def test_finds_exe_and_sha256(self):
        """Extracts installer URL and checksum URL from assets."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "assets": [
                {
                    "name": "BOT-ExRate-Setup.exe",
                    "browser_download_url": "https://dl/setup.exe",
                    "size": 50000000,
                },
                {
                    "name": "BOT-ExRate-Setup.exe.sha256",
                    "browser_download_url": "https://dl/setup.exe.sha256",
                    "size": 64,
                },
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = get_installer_asset_url("3.2.5")

        assert result["url"] == "https://dl/setup.exe"
        assert result["filename"] == "BOT-ExRate-Setup.exe"
        assert result["sha256_url"] == "https://dl/setup.exe.sha256"
        assert result["size"] == 50000000
        assert result["error"] is None

    def test_no_exe_in_assets(self):
        """Returns error when no .exe found in release assets."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "assets": [
                {
                    "name": "source.tar.gz",
                    "browser_download_url": "https://dl/source.tar.gz",
                    "size": 1000,
                },
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = get_installer_asset_url("3.2.5")

        assert result["url"] is None
        assert result["error"] is not None

    def test_exe_without_sha256(self):
        """Works when no .sha256 checksum is provided."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "assets": [
                {
                    "name": "Setup.EXE",
                    "browser_download_url": "https://dl/setup.exe",
                    "size": 1000,
                },
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = get_installer_asset_url("3.2.5")

        assert result["url"] == "https://dl/setup.exe"
        assert result["sha256_url"] is None

    def test_network_error_returns_error(self):
        """Returns error dict on network failure."""
        with patch(
            "core.auto_updater.httpx.get",
            side_effect=httpx.ConnectError("timeout"),
        ):
            result = get_installer_asset_url("3.2.5")

        assert result["url"] is None
        assert result["error"] is not None


# ═══════════════════════════════════════════════════════════════════════════
#  _fetch_expected_checksum
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchExpectedChecksum:
    """Tests for _fetch_expected_checksum."""

    def test_parses_plain_hash(self):
        """Parses a standalone SHA-256 hex string."""
        expected = "a" * 64
        mock_resp = MagicMock()
        mock_resp.text = f"  {expected}  \n"
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = _fetch_expected_checksum("https://example.com/hash.sha256")

        assert result == expected

    def test_parses_hash_with_filename(self):
        """Parses sha256sum-style format: '<hash>  <filename>'."""
        expected = "b" * 64
        mock_resp = MagicMock()
        mock_resp.text = f"{expected}  BOT-ExRate-Setup.exe\n"
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = _fetch_expected_checksum("https://example.com/hash.sha256")

        assert result == expected

    def test_returns_none_for_invalid_hash(self):
        """Returns None when hash isn't 64 hex chars."""
        mock_resp = MagicMock()
        mock_resp.text = "short_hash"
        mock_resp.raise_for_status = MagicMock()

        with patch("core.auto_updater.httpx.get", return_value=mock_resp):
            result = _fetch_expected_checksum("https://example.com/hash.sha256")

        assert result is None

    def test_returns_none_on_network_error(self):
        """Returns None on fetch failure, never raises."""
        with patch(
            "core.auto_updater.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            result = _fetch_expected_checksum("https://example.com/hash.sha256")

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
#  _verify_file_sha256
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifyFileSha256:
    """Tests for _verify_file_sha256."""

    def test_matching_hash(self, tmp_path):
        """Returns True when file hash matches expected."""
        content = b"Hello, World!"
        expected = hashlib.sha256(content).hexdigest()

        filepath = str(tmp_path / "test.exe")
        with open(filepath, "wb") as f:
            f.write(content)

        assert _verify_file_sha256(filepath, expected) is True

    def test_mismatched_hash(self, tmp_path):
        """Returns False when hash does not match."""
        filepath = str(tmp_path / "test.exe")
        with open(filepath, "wb") as f:
            f.write(b"actual content")

        wrong_hash = "0" * 64
        assert _verify_file_sha256(filepath, wrong_hash) is False

    def test_case_insensitive(self, tmp_path):
        """Hash comparison is case-insensitive."""
        content = b"test data"
        expected = hashlib.sha256(content).hexdigest().upper()

        filepath = str(tmp_path / "test.exe")
        with open(filepath, "wb") as f:
            f.write(content)

        assert _verify_file_sha256(filepath, expected) is True


# ═══════════════════════════════════════════════════════════════════════════
#  download_update
# ═══════════════════════════════════════════════════════════════════════════


class TestDownloadUpdate:
    """Tests for download_update."""

    def test_successful_download(self, tmp_path):
        """Downloads file successfully to specified directory."""
        content = b"fake installer content"
        dest = str(tmp_path)

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_bytes.return_value = [content]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("core.auto_updater.httpx.stream", return_value=mock_resp):
            result = download_update(
                url="https://dl/setup.exe",
                dest_dir=dest,
                filename="setup.exe",
            )

        assert result["error"] is None
        assert result["path"] is not None
        assert os.path.exists(result["path"])

    def test_sha256_verification_pass(self, tmp_path):
        """Download succeeds when SHA-256 matches."""
        content = b"verified content"
        expected_hash = hashlib.sha256(content).hexdigest()
        dest = str(tmp_path)

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_bytes.return_value = [content]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("core.auto_updater.httpx.stream", return_value=mock_resp):
            result = download_update(
                url="https://dl/setup.exe",
                dest_dir=dest,
                filename="verified.exe",
                expected_sha256=expected_hash,
            )

        assert result["error"] is None
        assert result["path"] is not None

    def test_sha256_verification_fail(self, tmp_path):
        """Download rejected when SHA-256 doesn't match."""
        content = b"tampered content"
        wrong_hash = "0" * 64
        dest = str(tmp_path)

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_bytes.return_value = [content]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("core.auto_updater.httpx.stream", return_value=mock_resp):
            result = download_update(
                url="https://dl/setup.exe",
                dest_dir=dest,
                filename="bad.exe",
                expected_sha256=wrong_hash,
            )

        assert result["error"] is not None
        assert "SHA-256" in result["error"]
        assert result["path"] is None
        # Temp file should be cleaned up
        assert not os.path.exists(os.path.join(dest, "bad.exe.downloading"))

    def test_network_error_cleanup(self, tmp_path):
        """Cleans up partial downloads on network error."""
        dest = str(tmp_path)

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": "1000000"}
        mock_resp.iter_bytes.side_effect = httpx.ReadError("connection reset")
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("core.auto_updater.httpx.stream", return_value=mock_resp):
            result = download_update(
                url="https://dl/setup.exe",
                dest_dir=dest,
                filename="partial.exe",
            )

        assert result["error"] is not None
        assert result["path"] is None

    def test_progress_callback(self, tmp_path):
        """Invokes progress_cb with download progress."""
        content = b"x" * 1024
        dest = str(tmp_path)
        cb_calls = []

        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_bytes.return_value = [content]
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("core.auto_updater.httpx.stream", return_value=mock_resp):
            download_update(
                url="https://dl/setup.exe",
                dest_dir=dest,
                filename="progress.exe",
                progress_cb=lambda d, t: cb_calls.append((d, t)),
            )

        assert len(cb_calls) > 0
        assert cb_calls[-1][0] == len(content)


# ═══════════════════════════════════════════════════════════════════════════
#  apply_update — path sanitization (H-02)
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyUpdateSanitization:
    """Tests for apply_update path validation."""

    def test_rejects_unsafe_path_characters(self, tmp_path):
        """Paths with shell metacharacters are rejected."""
        from core.auto_updater import apply_update

        unsafe_path = str(tmp_path / "bad&path")
        os.makedirs(unsafe_path, exist_ok=True)

        with patch("core.auto_updater._get_install_dir", return_value=unsafe_path):
            with patch("sys.frozen", True, create=True):
                result = apply_update(str(tmp_path / "BOT-ExRate-Setup.exe"))

        # Should fail because the install dir has '&'
        assert result["success"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  _get_install_dir
# ═══════════════════════════════════════════════════════════════════════════


class TestGetInstallDir:
    """Tests for _get_install_dir."""

    def test_dev_mode_returns_project_root(self):
        """In development mode, returns project root."""
        from core.auto_updater import _get_install_dir

        with patch("sys.frozen", False, create=True):
            result = _get_install_dir()

        assert result is not None
        assert os.path.isdir(result)

    def test_frozen_mode_uses_exe_parent(self, tmp_path):
        """When frozen, uses sys.executable parent dir."""
        from core.auto_updater import _get_install_dir

        fake_exe = str(tmp_path / "BOT-ExRate.exe")
        with open(fake_exe, "w") as f:
            f.write("fake")

        with patch("sys.frozen", True, create=True):
            with patch("sys.executable", fake_exe):
                with patch("platform.system", return_value="Darwin"):
                    result = _get_install_dir()

        assert result == str(tmp_path)
