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
from urllib.parse import urlparse

import httpx
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# SECURITY (SSRF): only these hosts may be fetched for update assets.
# GitHub serves release binaries from objects/release-assets subdomains.
_ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})


def _is_allowed_download_url(url: str) -> bool:
    """Return True only for https URLs whose host is in the allowlist.

    Used to block SSRF: a tampered GitHub API response (or a malicious
    redirect) could otherwise point the downloader at an arbitrary host.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return False
    if parsed.scheme != "https":
        return False
    return (parsed.hostname or "").lower() in _ALLOWED_DOWNLOAD_HOSTS

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
    if not _is_allowed_download_url(sha256_url):
        logger.warning("Refusing checksum fetch from non-allowlisted host: %s", sha256_url)
        return None
    try:
        # follow_redirects=False + manual host validation prevents a
        # redirect from leading us off the allowlist (SSRF).
        resp = httpx.get(sha256_url, timeout=10.0, follow_redirects=False)
        while resp.is_redirect:
            location = resp.headers.get("location", "")
            if not _is_allowed_download_url(location):
                logger.warning("Checksum redirect to non-allowlisted host blocked: %s", location)
                return None
            resp = httpx.get(location, timeout=10.0, follow_redirects=False)
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
    if actual != expected_hash.lower():
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

    SECURITY (integrity, HIGH): expected_sha256 is now MANDATORY. If it is
    absent — or does not match — the download is rejected and the installer
    is NEVER executed. There is currently no detached-signature mechanism on
    the release pipeline, so the residual limitation is that integrity rests
    on the sha256 published alongside the release; a sufficiently capable
    MITM that controls BOTH the binary and its .sha256 over TLS could still
    substitute a matching pair. TLS + the host allowlist below mitigate this.

    SECURITY (SSRF, HIGH): the url and every redirect hop must be https and
    on the host allowlist. follow_redirects is disabled and each hop is
    validated manually.

    The Inno Setup installer will then copy files to the correct
    install directory via the /DIR= flag.

    Args:
        url: Direct download URL for the .exe
        dest_dir: Where to save (defaults to system temp dir)
        filename: Override filename (defaults to URL basename)
        progress_cb: Optional callback(downloaded_bytes, total_bytes)
        expected_sha256: REQUIRED hex SHA-256 hash to verify download

    Returns:
        {"path": str | None, "error": str | None}
    """
    result = {"path": None, "error": None}

    # SECURITY: integrity verification is mandatory. Refuse to download (and
    # therefore refuse to ever run) a binary we cannot verify.
    if not expected_sha256:
        result["error"] = (
            "Refusing update: no SHA-256 checksum available for integrity "
            "verification. The release must publish a .sha256 checksum."
        )
        logger.error("download_update blocked: missing expected_sha256")
        return result

    # SECURITY: enforce https + host allowlist before fetching.
    if not _is_allowed_download_url(url):
        result["error"] = f"Refusing update: download host not allowed: {url}"
        logger.error("download_update blocked non-allowlisted URL: %s", url)
        return result

    # SECURITY (TOCTOU): download into a PRIVATE per-run directory created
    # 0700 instead of the shared temp root, so another local user cannot
    # pre-create / race / read the downloaded installer before it runs.
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="bot_exrate_dl_")
        try:
            os.chmod(dest_dir, 0o700)
        except OSError:
            pass

    if filename is None:
        filename = url.rsplit("/", 1)[-1] if "/" in url else "update.exe"

    # Download with a .tmp suffix to avoid partial overwrites
    tmp_filename = filename + ".downloading"
    tmp_path = os.path.join(dest_dir, tmp_filename)
    final_path = os.path.join(dest_dir, filename)

    try:
        # SECURITY: follow_redirects=False; validate each hop against the
        # allowlist so a redirect cannot smuggle us to an arbitrary host.
        stream_url = url
        for _ in range(5):  # bounded redirect chain
            with httpx.stream(
                "GET", stream_url, follow_redirects=False, timeout=120.0
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not _is_allowed_download_url(location):
                        result["error"] = (
                            f"Refusing update: redirect to non-allowlisted "
                            f"host: {location}"
                        )
                        logger.error(
                            "download_update blocked redirect: %s", location
                        )
                        return result
                    stream_url = location
                    continue
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(downloaded, total)
                break
        else:
            # Redirect chain exceeded the bound without a final response.
            result["error"] = "Refusing update: too many redirects"
            logger.error("download_update blocked: redirect limit exceeded")
            return result

        # SECURITY: integrity is mandatory — verify or reject.
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
    expected_sha256: Optional[str] = None,
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

    SECURITY (integrity TOCTOU, HIGH): the file is RE-VERIFIED against
    expected_sha256 immediately before it is executed. download_update may
    have verified the file earlier, but the file could be swapped between
    download and launch (time-of-check / time-of-use). expected_sha256 is
    MANDATORY: a missing hash, a missing file, or a mismatch refuses the
    update and NEVER runs the binary. There is no verification-skipping path.

    Args:
        new_exe_path: Absolute path to the downloaded installer/exe.
        install_dir: Override install directory. If None, auto-resolved.
        expected_sha256: REQUIRED hex SHA-256 of the installer. Re-checked
            here right before execution.

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

    # SECURITY (integrity, MANDATORY): re-verify the file hash right before
    # we execute it. No fallback that skips verification.
    if not expected_sha256:
        result["error"] = (
            "Refusing to apply update: no SHA-256 checksum supplied for "
            "pre-execution integrity verification."
        )
        logger.error("apply_update blocked: missing expected_sha256")
        return result
    if not os.path.isfile(new_exe_path):
        result["error"] = (
            f"Refusing to apply update: installer not found at {new_exe_path}"
        )
        logger.error("apply_update blocked: installer missing before exec")
        return result
    if not _verify_file_sha256(new_exe_path, expected_sha256):
        result["error"] = (
            "Refusing to apply update: installer SHA-256 mismatch immediately "
            "before execution. The file may have been tampered with."
        )
        logger.error("apply_update blocked: re-hash mismatch before exec")
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

            # SECURITY (TOCTOU): write the helper script into a private
            # per-run directory created with mode 0700 instead of a fixed,
            # predictable path in the shared temp dir. This prevents another
            # local user from pre-creating / racing the .bat file. Verify the
            # directory is owned by us before using it.
            work_dir = tempfile.mkdtemp(prefix="bot_exrate_upd_")
            try:
                os.chmod(work_dir, 0o700)
            except OSError:
                pass
            try:
                st = os.stat(work_dir)
                if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
                    result["error"] = "Update workspace ownership check failed"
                    logger.error("apply_update: temp dir not owned by current user")
                    return result
            except OSError as e:
                result["error"] = f"Could not stat update workspace: {e}"
                return result

            bat_path = os.path.join(work_dir, "bot_exrate_updater.bat")

            # H-02: Validate paths — reject shell metacharacters
            _UNSAFE_CHARS = set('&|<>^%!')
            for _path in (new_exe_path, install_dir, current_exe):
                if _UNSAFE_CHARS.intersection(_path):
                    result["error"] = (
                        f"Unsafe characters in path: {_path}"
                    )
                    return result

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

            # SECURITY: restrict the helper script to the owner only.
            try:
                os.chmod(bat_path, 0o600)
            except OSError:
                pass

            if sys.platform == "win32":
                flags = 0x00000008  # DETACHED_PROCESS
                subprocess.Popen(
                    ["cmd.exe", "/c", bat_path],
                    creationflags=flags,
                    close_fds=True,
                )
            else:
                import shlex
                subprocess.Popen(
                    [
                        "sh", "-c",
                        f"sleep 3 && {shlex.quote(new_exe_path)} /VERYSILENT "
                        f"/DIR={shlex.quote(install_dir)} && "
                        f"open {shlex.quote(current_exe)}",
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
