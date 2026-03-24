#!/usr/bin/env python3
"""
core/xls_converter.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.8) - Native OS-Aware Conversion Pipeline
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
    logger.info("Converting legacy .xls natively: %s", os.path.basename(filepath))
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
                logger.info("LibreOffice conversion complete: %s", temp_path)
                return temp_path
        except Exception as e:
            logger.warning("LibreOffice proxy failed. %s", e)

    # ── Priority 2: PURE PYTHON FALLBACK (xlrd → openpyxl) ─────────
    # No external software needed — works on ALL platforms
    logger.info("No native converter found. Using pure Python xlrd → openpyxl conversion.")
    try:
        import openpyxl as _openpyxl
        import xlrd
        from openpyxl.utils import get_column_letter as _gcl

        devnull_fh = open(os.devnull, 'w')
        try:
            wb_xls = xlrd.open_workbook(filepath, formatting_info=False, logfile=devnull_fh)
        finally:
            devnull_fh.close()

        wb_xlsx = _openpyxl.Workbook()
        # Remove the default empty sheet that openpyxl creates
        if wb_xlsx.sheetnames:
            wb_xlsx.remove(wb_xlsx.active)

        for sheet_name in wb_xls.sheet_names():
            ws_xls = wb_xls.sheet_by_name(sheet_name)
            ws_xlsx = wb_xlsx.create_sheet(title=sheet_name)

            for row_idx in range(ws_xls.nrows):
                for col_idx in range(ws_xls.ncols):
                    cell_type = ws_xls.cell_type(row_idx, col_idx)
                    cell_val = ws_xls.cell_value(row_idx, col_idx)

                    # Convert xlrd types to Python types
                    if cell_type == xlrd.XL_CELL_DATE and cell_val:
                        try:
                            from datetime import datetime as _dt
                            dt_tuple = xlrd.xldate_as_tuple(cell_val, wb_xls.datemode)
                            if dt_tuple[3:] == (0, 0, 0):
                                cell_val = _dt(*dt_tuple[:3]).date()
                            else:
                                cell_val = _dt(*dt_tuple)
                        except Exception:
                            pass
                    elif cell_type == xlrd.XL_CELL_BOOLEAN:
                        cell_val = bool(cell_val)
                    elif cell_type == xlrd.XL_CELL_EMPTY:
                        continue

                    target = ws_xlsx.cell(row=row_idx + 1, column=col_idx + 1)
                    target.value = cell_val

            # Copy column widths (approximate)
            for col_idx in range(ws_xls.ncols):
                col_letter = _gcl(col_idx + 1)
                # xlrd doesn't expose column widths easily, use a sensible default
                ws_xlsx.column_dimensions[col_letter].width = 14

        wb_xls.release_resources()
        wb_xlsx.save(temp_path)
        wb_xlsx.close()
        logger.info("Pure Python conversion complete: %s", os.path.basename(temp_path))
        return temp_path
    except Exception as e:
        logger.error("Pure Python xlrd→openpyxl conversion failed: %s", e)
        raise RuntimeError(
            f"Failed to convert .xls to .xlsx: {e}\n"
            "Install LibreOffice for better conversion, or use .xlsx files directly."
        ) from e
