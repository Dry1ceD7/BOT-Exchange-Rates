#!/usr/bin/env python3
"""
core/auto_updater.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Auto-Updater Engine
---------------------------------------------------------------------------
On boot, pings the GitHub Releases API to check if a newer version is
available. Returns a structured dict for the GUI to render a non-intrusive
notification.

v3.1.2 — Fixed install path resolution:
  - Reads install directory from Windows Registry (Inno Setup record)
  - Falls back to sys.executable parent for portable/non-registry installs
  - Downloads to %TEMP% to avoid permission issues on server shares
  - Passes correct /DIR= to Inno Setup for in-place updates

Security: Read-only GET request. No tokens required for public repos.
"""

import hashlib
import logging
import os
import platform
import tempfile
from typing import Optional

import httpx
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

GITHUB_RELEASES_URL = (
    "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases/latest"
)
GITHUB_ALL_RELEASES_URL = (
    "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"
)

# Inno Setup AppId (must match installer.iss)
INNO_APP_ID = "{B0T-EXRATE-2026-AAE}_is1"


def _get_install_dir() -> Optional[str]:
    """
    Resolve the actual installation directory.

    Priority order:
      1. Windows Registry (Inno Setup records InstallLocation)
      2. sys.executable parent directory (PyInstaller frozen app)
      3. Project root (development mode)

    This is the KEY FIX: when the app is installed on a server share
    (e.g., \\\\SERVER\\Apps\\BOT-ExRate), the registry stores the exact
    path where Inno Setup installed it. Using sys.executable alone
    can return incorrect paths on mapped drives or UNC shares.
    """
    import sys

    # ── Strategy 1: Windows Registry (most reliable for Inno Setup) ──
    if platform.system() == "Windows":
        try:
            import winreg

            # Inno Setup writes to either HKLM or HKCU depending on
            # PrivilegesRequired. We check HKCU first (lowest), then HKLM.
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    key_path = (
                        f"Software\\Microsoft\\Windows\\CurrentVersion"
                        f"\\Uninstall\\{INNO_APP_ID}"
                    )
                    with winreg.OpenKey(hive, key_path) as key:
                        install_loc, _ = winreg.QueryValueEx(
                            key, "InstallLocation"
                        )
                        if install_loc and os.path.isdir(install_loc):
                            logger.info(
                                "Install dir from registry: %s", install_loc
                            )
                            return install_loc.rstrip("\\").rstrip("/")
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
        except ImportError:
            # winreg not available (non-Windows or restricted env)
            pass

    # ── Strategy 2: sys.executable parent (frozen apps) ──────────────
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        logger.info("Install dir from sys.executable: %s", exe_dir)
        return exe_dir

    # ── Strategy 3: Development mode — project root ──────────────────
    dev_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logger.info("Install dir from dev root: %s", dev_root)
    return dev_root


def check_for_update(
    current_version: Optional[str] = None,
    include_prerelease: bool = False,
) -> dict:
    """
    Check whether a newer release is available on GitHub.

    Args:
        current_version: The running app version (e.g., "2.6.1").
                         If None, reads from pyproject.toml or defaults.
        include_prerelease: If True, also considers pre-release/beta versions.

    Returns:
        {
            "update_available": bool,
            "latest_version": str | None,
            "download_url": str | None,
            "is_prerelease": bool,
            "error": str | None,
        }
    """
    result = {
        "update_available": False,
        "latest_version": None,
        "download_url": None,
        "is_prerelease": False,
        "error": None,
    }

    if current_version is None:
        current_version = "0.0.0"

    try:
        if include_prerelease:
            # Fetch all releases and find the newest one
            resp = httpx.get(
                GITHUB_ALL_RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=8.0,
                params={"per_page": 10},
            )
            resp.raise_for_status()
            releases = resp.json()
            if not releases:
                result["error"] = "No releases found"
                return result
            # First release is the most recent
            data = releases[0]
        else:
            resp = httpx.get(
                GITHUB_RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()

        tag = data.get("tag_name", "")
        # Strip leading 'v' if present (e.g., "v3.0.0" -> "3.0.0")
        tag_clean = tag.lstrip("vV")

        result["latest_version"] = tag_clean
        result["download_url"] = data.get("html_url")
        result["is_prerelease"] = data.get("prerelease", False)

        try:
            remote = Version(tag_clean)
            local = Version(current_version.lstrip("vV"))
            result["update_available"] = remote > local
        except InvalidVersion as e:
            logger.warning("Version parse error: %s", e)
            result["error"] = f"Version parse error: {e}"

    except httpx.HTTPStatusError as e:
        logger.warning("GitHub API returned %s: %s", e.response.status_code, e)
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        logger.warning("Network error checking for updates: %s", e)
        result["error"] = f"Network error: {e}"
    except (ValueError, KeyError, OSError) as e:
        logger.warning("Unexpected error in auto-updater: %s", e)
        result["error"] = str(e)

    return result


def get_installer_asset_url(tag: str) -> dict:
    """
    Fetch the installer .exe asset URL from a specific GitHub release.

    Also looks for a .sha256 checksum file in the release assets for
    integrity verification.

    Returns:
        {"url": str | None, "filename": str | None,
         "size": int | None, "sha256_url": str | None, "error": str | None}
    """
    result = {
        "url": None, "filename": None, "size": None,
        "sha256_url": None, "error": None,
    }
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/Dry1ceD7/"
            f"BOT-Exchange-Rates/releases/tags/v{tag}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # Look for .exe installer asset and optional .sha256 checksum
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe"):
                result["url"] = asset.get("browser_download_url")
                result["filename"] = name
                result["size"] = asset.get("size", 0)
            elif name.lower().endswith(".sha256"):
                result["sha256_url"] = asset.get("browser_download_url")

        if result["url"] is None:
            result["error"] = "No .exe installer found in release assets"
    except httpx.HTTPStatusError as e:
        logger.warning("GitHub API returned %s: %s", e.response.status_code, e)
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        logger.warning("Failed to get installer asset: %s", e)
        result["error"] = str(e)
    return result


def _fetch_expected_checksum(sha256_url: str) -> Optional[str]:
    """Download and parse a .sha256 checksum file.

    The file is expected to contain a single SHA-256 hex digest
    (optionally followed by a filename, like sha256sum output).
    Returns the hex digest string, or None on any error.
    """
    try:
        resp = httpx.get(sha256_url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        # Format: "abcdef123456...  filename.exe" or just "abcdef123456..."
        line = resp.text.strip().split()[0]
        if len(line) == 64:  # valid SHA-256 hex length
            return line.lower()
    except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
        logger.warning("Could not fetch checksum from %s: %s", sha256_url, e)
    return None


def _verify_file_sha256(filepath: str, expected_hash: str) -> bool:
    """Verify that a file's SHA-256 matches the expected hash."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest().lower()
    if actual != expected_hash:
        logger.error(
            "Checksum mismatch! Expected %s, got %s", expected_hash, actual
        )
        return False
    logger.info("SHA-256 checksum verified: %s", actual)
    return True


def download_update(
    url: str,
    dest_dir: Optional[str] = None,
    filename: Optional[str] = None,
    progress_cb=None,
    expected_sha256: Optional[str] = None,
) -> dict:
    """
    Download the update installer to a temp directory.

    v3.1.2 FIX: Always downloads to %TEMP% (or /tmp) to avoid:
      - Permission denied errors on server shares
      - File lock conflicts with the running application
      - Writing into the inner PyInstaller _MEIXXXXXX folder

    v3.2.2: If expected_sha256 is provided, verify file integrity
    after download. The download is rejected if the hash does not match.

    The Inno Setup installer will then copy files to the correct
    install directory via the /DIR= flag.

    Args:
        url: Direct download URL for the .exe
        dest_dir: Where to save (defaults to system temp dir)
        filename: Override filename (defaults to URL basename)
        progress_cb: Optional callback(downloaded_bytes, total_bytes)
        expected_sha256: Optional hex SHA-256 hash to verify download

    Returns:
        {"path": str | None, "error": str | None}
    """
    result = {"path": None, "error": None}

    # v3.1.2: Always download to temp directory
    if dest_dir is None:
        dest_dir = tempfile.gettempdir()

    if filename is None:
        filename = url.rsplit("/", 1)[-1] if "/" in url else "update.exe"

    # Download with a .tmp suffix to avoid partial overwrites
    tmp_filename = filename + ".downloading"
    tmp_path = os.path.join(dest_dir, tmp_filename)
    final_path = os.path.join(dest_dir, filename)

    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=120.0
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded, total)

        # Verify integrity if a checksum was provided
        if expected_sha256:
            if not _verify_file_sha256(tmp_path, expected_sha256):
                os.remove(tmp_path)
                result["error"] = (
                    "Download integrity check failed (SHA-256 mismatch). "
                    "The file may be corrupted or tampered with."
                )
                return result

        # Rename from .downloading to final filename
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(tmp_path, final_path)

        result["path"] = final_path
        logger.info("Update downloaded to: %s", final_path)
    except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
        logger.error("Download failed: %s", e)
        result["error"] = str(e)
        # Cleanup partial download
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return result


def apply_update(
    new_exe_path: str,
    install_dir: Optional[str] = None,
) -> dict:
    """
    Install the downloaded update silently.

    For Inno Setup installers (BOT-ExRate-Setup.exe):
      Runs the installer with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
      /DIR=<resolved_install_dir> to install behind the scenes.

    v3.1.2 FIX: install_dir is resolved from the Windows Registry
    (where Inno Setup originally recorded it), ensuring the update
    goes to the server share — NOT the user's local PC.

    For portable single-file builds:
      Falls back to atomic exe swap.

    Args:
        new_exe_path: Absolute path to the downloaded installer/exe.
        install_dir: Override install directory. If None, auto-resolved.

    Returns:
        {"success": bool, "error": str | None,
         "install_dir": str | None}
    """
    import subprocess
    import sys

    result = {"success": False, "error": None, "install_dir": None}

    if not getattr(sys, "frozen", False):
        result["error"] = (
            "In-place update only works for frozen (packaged) apps"
        )
        return result

    # v3.1.2: Resolve install directory from registry first
    if install_dir is None:
        install_dir = _get_install_dir()

    if install_dir is None:
        result["error"] = "Could not determine install directory"
        return result

    result["install_dir"] = install_dir
    current_exe = os.path.join(install_dir, "BOT-ExRate.exe")
    filename = os.path.basename(new_exe_path).lower()

    try:
        if "setup" in filename:
            # Inno Setup installer — run via detached batch script.
            # This allows the Python process to exit fully before the
            # installer runs, preventing "Code 5: Access Denied" errors.
            logger.info(
                "Preparing detached installer script for dir: %s",
                install_dir,
            )

            bat_path = os.path.join(
                tempfile.gettempdir(), "bot_exrate_updater.bat"
            )
            with open(bat_path, "w") as f:
                f.write("@echo off\n")
                # Wait 3s for app to exit fully
                f.write("timeout /t 3 /nobreak > NUL\n")
                # Run Inno Setup with /DIR pointing to the REAL install dir
                f.write(
                    f'"{new_exe_path}" /VERYSILENT /SUPPRESSMSGBOXES '
                    f'/NORESTART /DIR="{install_dir}"\n'
                )
                # Wait for installer to finish
                f.write("timeout /t 2 /nobreak > NUL\n")
                # Relaunch the app from the install dir
                f.write(f'start "" "{current_exe}"\n')
                # Self-delete batch file
                f.write('del "%~f0"\n')

            if sys.platform == "win32":
                flags = 0x00000008  # DETACHED_PROCESS
                subprocess.Popen(
                    ["cmd.exe", "/c", bat_path],
                    creationflags=flags,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [
                        "sh", "-c",
                        f"sleep 3 && '{new_exe_path}' /VERYSILENT "
                        f"/DIR='{install_dir}' && "
                        f"open '{current_exe}'",
                    ],
                    start_new_session=True,
                )

            result["success"] = True
            logger.info("Detached updater script launched successfully")
        else:
            # Portable exe — atomic swap
            backup_path = current_exe + ".bak"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(current_exe, backup_path)
            os.rename(new_exe_path, current_exe)
            result["success"] = True
            logger.info("Atomic exe swap completed")
    except PermissionError:
        logger.error("Permission denied — cannot write to app directory.")
        result["error"] = (
            "Permission denied. Please ask your IT admin to update "
            "the application on the server."
        )
    except subprocess.TimeoutExpired:
        result["error"] = "Installer timed out after 120 seconds"
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.error("Update apply failed: %s", e)
        result["error"] = str(e)

    return result


def restart_app() -> None:
    """
    Restart the application by launching the executable again
    and exiting the current process.
    """
    import subprocess
    import sys

    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable] + sys.argv)

    sys.exit(0)
