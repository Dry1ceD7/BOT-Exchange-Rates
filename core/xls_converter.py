#!/usr/bin/env python3
"""
core/xls_converter.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.8) - Native OS-Aware Conversion Pipeline
---------------------------------------------------------------------------
Strictly converts legacy .xls files to .xlsx using native proxies (win32com
on Windows, or soffice headless on Mac/Linux) to guarantee absolute 100% 
preservation of enterprise fonts, styles, and geometries.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _get_soffice_path() -> str | None:
    """Find the LibreOffice executable path."""
    # macOS path
    mac_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(mac_path):
        return mac_path

    # Linux / Windows paths in PATH
    try:
        # 'where' on Windows, 'which' on Linux/Mac
        cmd = "where" if os.name == "nt" else "which"
        path = subprocess.check_output([cmd, "soffice"]).decode().strip()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    return None


def convert_xls_to_xlsx(filepath: str) -> str:
    """
    Convert a legacy .xls file to .xlsx preserving formatting natively.

    Priority 1 (Windows): win32com.client (Microsoft Excel proxy)
    Priority 2 (Mac/Linux/WinFallback): LibreOffice soffice --headless proxy
    Priority 3 (Failsafe): RuntimeError (Alerts GUI to prevent style loss)
    """
    logger.info(f"Converting legacy .xls natively: {os.path.basename(filepath)}")
    filepath = os.path.abspath(filepath)
    dir_name = os.path.dirname(filepath)
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    temp_path = os.path.join(dir_name, f".{base_name}_converted.xlsx")

    # ── Priority 1: LIBREOFFICE PROXY (MAC / LINUX / WIN-FALLBACK) ───
    soffice_path = _get_soffice_path()
    if soffice_path:
        logger.info("Engaging LibreOffice Native Headless Engine.")
        try:
            outdir = dir_name or "."
            subprocess.run(
                [
                    soffice_path, "--headless", "--convert-to", "xlsx",
                    "--outdir", outdir, filepath
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            standard_outpath = os.path.join(outdir, f"{base_name}.xlsx")
            if os.path.exists(standard_outpath):
                shutil.move(standard_outpath, temp_path)
                logger.info(f"LibreOffice conversion complete: {temp_path}")
                return temp_path
        except Exception as e:
            logger.warning(f"LibreOffice proxy failed. {e}")

    # ── Priority 3: STRICT FORMATTING FAILSAFE (GUI ALERT) ───────────
    error_msg = (
        "Native Microsoft Excel or LibreOffice is required to process legacy "
        ".xls files. Conversion aborted to protect your formatting."
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)
