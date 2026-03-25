#!/usr/bin/env python3
"""
core/auto_updater.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.8) — Auto-Updater Engine
---------------------------------------------------------------------------
On boot, pings the GitHub Releases API to check if a newer version is
available. Returns a structured dict for the GUI to render a non-intrusive
notification.

Security: Read-only GET request. No tokens required for public repos.
"""

import logging
import os
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
    except Exception as e:
        logger.warning("Unexpected error in auto-updater: %s", e)
        result["error"] = str(e)

    return result


def get_installer_asset_url(tag: str) -> dict:
    """
    Fetch the installer .exe asset URL from a specific GitHub release.

    Returns:
        {"url": str | None, "filename": str | None, "size": int | None, "error": str | None}
    """
    result = {"url": None, "filename": None, "size": None, "error": None}
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases/tags/v{tag}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # Look for .exe installer asset
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe"):
                result["url"] = asset.get("browser_download_url")
                result["filename"] = name
                result["size"] = asset.get("size", 0)
                return result

        result["error"] = "No .exe installer found in release assets"
    except Exception as e:
        logger.warning("Failed to get installer asset: %s", e)
        result["error"] = str(e)
    return result


def download_update(
    url: str,
    dest_dir: Optional[str] = None,
    filename: Optional[str] = None,
    progress_cb=None,
) -> dict:
    """
    Download the update executable.

    By default, downloads to the SAME directory where the running
    application lives (server share path). This ensures updates
    are installed on the server, not on the user's local PC.

    Args:
        url: Direct download URL for the .exe
        dest_dir: Where to save (defaults to app's own directory)
        filename: Override filename (defaults to URL basename)
        progress_cb: Optional callback(downloaded_bytes, total_bytes)

    Returns:
        {"path": str | None, "error": str | None}
    """
    import sys

    result = {"path": None, "error": None}

    if dest_dir is None:
        # Use the directory of the running executable (server path)
        if getattr(sys, "frozen", False):
            # PyInstaller frozen app — use exe's directory
            dest_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            # Development mode — use project root
            dest_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )

    if filename is None:
        filename = url.rsplit("/", 1)[-1] if "/" in url else "update.exe"

    # Download with a .tmp suffix to avoid partial overwrites
    tmp_filename = filename + ".downloading"
    tmp_path = os.path.join(dest_dir, tmp_filename)
    final_path = os.path.join(dest_dir, filename)

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded, total)

        # Rename from .downloading to final filename
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(tmp_path, final_path)

        result["path"] = final_path
        logger.info("Update downloaded to: %s", final_path)
    except Exception as e:
        logger.error("Download failed: %s", e)
        result["error"] = str(e)
        # Cleanup partial download
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return result


def apply_update(new_exe_path: str) -> dict:
    """
    Replace the running executable with the downloaded update.

    Steps:
      1. Rename current exe → current.exe.bak (backup)
      2. Rename downloaded exe → current exe name (in-place swap)
      3. Return success status — caller should prompt restart

    This works because PyInstaller unpacks to a temp dir at launch,
    so the original .exe file is not locked on Windows.

    Args:
        new_exe_path: Absolute path to the downloaded new .exe

    Returns:
        {"success": bool, "backup_path": str | None, "error": str | None}
    """
    import sys

    result = {"success": False, "backup_path": None, "error": None}

    if not getattr(sys, "frozen", False):
        result["error"] = "In-place update only works for frozen (packaged) apps"
        return result

    current_exe = os.path.abspath(sys.executable)
    backup_path = current_exe + ".bak"

    try:
        # Remove old backup if it exists
        if os.path.exists(backup_path):
            os.remove(backup_path)

        # Step 1: Current exe → backup
        os.rename(current_exe, backup_path)
        result["backup_path"] = backup_path
        logger.info("Backed up current exe: %s → %s", current_exe, backup_path)

        # Step 2: New exe → current exe name
        os.rename(new_exe_path, current_exe)
        logger.info("Replaced with new exe: %s → %s", new_exe_path, current_exe)

        result["success"] = True
    except PermissionError:
        logger.error(
            "Permission denied — cannot write to app directory. "
            "The server share may be read-only for this user."
        )
        result["error"] = (
            "Permission denied. Please ask your IT admin to update "
            "the application on the server."
        )
        # Try to rollback if we already renamed the current exe
        if not os.path.exists(current_exe) and os.path.exists(backup_path):
            try:
                os.rename(backup_path, current_exe)
            except OSError:
                pass
    except Exception as e:
        logger.error("Update apply failed: %s", e)
        result["error"] = str(e)
        # Try to rollback
        if not os.path.exists(current_exe) and os.path.exists(backup_path):
            try:
                os.rename(backup_path, current_exe)
            except OSError:
                pass

    return result

