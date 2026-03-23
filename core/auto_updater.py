#!/usr/bin/env python3
"""
core/auto_updater.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.7) — Auto-Updater Engine
---------------------------------------------------------------------------
On boot, pings the GitHub Releases API to check if a newer version is
available. Returns a structured dict for the GUI to render a non-intrusive
notification.

Security: Read-only GET request. No tokens required for public repos.
"""

import logging
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
