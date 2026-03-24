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


def check_for_update(
    current_version: Optional[str] = None,
) -> dict:
    """
    Check whether a newer release is available on GitHub.

    Args:
        current_version: The running app version (e.g., "2.6.1").
                         If None, reads from pyproject.toml or defaults.

    Returns:
        {
            "update_available": bool,
            "latest_version": str | None,
            "download_url": str | None,
            "error": str | None,
        }
    """
    result = {
        "update_available": False,
        "latest_version": None,
        "download_url": None,
        "error": None,
    }

    if current_version is None:
        current_version = "0.0.0"

    try:
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
    Download the update installer to a local directory.

    Args:
        url: Direct download URL for the installer .exe
        dest_dir: Where to save (defaults to temp dir)
        filename: Override filename (defaults to URL basename)
        progress_cb: Optional callback(downloaded_bytes, total_bytes)

    Returns:
        {"path": str | None, "error": str | None}
    """
    import tempfile

    result = {"path": None, "error": None}
    if dest_dir is None:
        dest_dir = tempfile.gettempdir()

    if filename is None:
        filename = url.rsplit("/", 1)[-1] if "/" in url else "update.exe"

    dest_path = os.path.join(dest_dir, filename)

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded, total)

        result["path"] = dest_path
        logger.info("Update downloaded to: %s", dest_path)
    except Exception as e:
        logger.error("Download failed: %s", e)
        result["error"] = str(e)
    return result
