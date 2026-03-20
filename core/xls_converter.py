#!/usr/bin/env python3
"""
core/xls_converter.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.7) - Legacy .xls Conversion Pipeline
---------------------------------------------------------------------------
Converts legacy .xls files to .xlsx using xlrd + openpyxl.
Preserves: fonts, colors, borders, alignment, column widths, row heights,
           merged cells, number formats, and cell styles.
"""

import logging
import os
import shutil
import subprocess

import openpyxl
import xlrd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# xlrd formatting helpers
# -------------------------------------------------------------------------

def _xlrd_font_to_openpyxl(xf, book) -> Font:
    """Convert an xlrd XF record's font to an openpyxl Font."""
    try:
        xlrd_font = book.font_list[xf.font_index]
        color = "000000"
        if (
            xlrd_font.colour_index is not None
            and xlrd_font.colour_index != 0x7FFF
        ):
            colour_map = book.colour_map.get(xlrd_font.colour_index)
            if colour_map and colour_map != (0, 0, 0):
                color = "{:02X}{:02X}{:02X}".format(*colour_map)
        return Font(
            name=xlrd_font.name,
            size=xlrd_font.height / 20 if xlrd_font.height else 11,
            bold=xlrd_font.bold,
            italic=xlrd_font.italic,
            underline="single" if xlrd_font.underlined else None,
            strike=xlrd_font.struck_out,
            color=color,
        )
    except (IndexError, AttributeError):
        return Font()


def _xlrd_alignment_to_openpyxl(xf) -> Alignment:
    """Convert xlrd XF alignment to openpyxl Alignment."""
    try:
        horz_map = {0: "general", 1: "left", 2: "center", 3: "right"}
        vert_map = {0: "top", 1: "center", 2: "bottom"}
        return Alignment(
            horizontal=horz_map.get(xf.alignment.hor_align, "general"),
            vertical=vert_map.get(xf.alignment.vert_align, "bottom"),
            wrap_text=bool(xf.alignment.text_wrap),
        )
    except AttributeError:
        return Alignment()


def _xlrd_border_side(border_line_style, colour_index, book) -> Side:
    """Convert a single xlrd border to an openpyxl Side."""
    style_map = {
        0: None, 1: "thin", 2: "medium", 3: "dashed",
        4: "dotted", 5: "thick", 6: "double", 7: "hair",
    }
    color = "000000"
    if colour_index is not None and colour_index != 0x7FFF:
        colour_map = book.colour_map.get(colour_index)
        if colour_map and colour_map != (0, 0, 0):
            color = "{:02X}{:02X}{:02X}".format(*colour_map)
    border_style = style_map.get(border_line_style)
    if not border_style:
        return Side()
    return Side(style=border_style, color=color)


def _xlrd_borders_to_openpyxl(xf, book) -> Border:
    """Convert xlrd XF borders to openpyxl Border."""
    try:
        b = xf.border
        return Border(
            left=_xlrd_border_side(
                b.left_line_style, b.left_colour_index, book
            ),
            right=_xlrd_border_side(
                b.right_line_style, b.right_colour_index, book
            ),
            top=_xlrd_border_side(
                b.top_line_style, b.top_colour_index, book
            ),
            bottom=_xlrd_border_side(
                b.bottom_line_style, b.bottom_colour_index, book
            ),
        )
    except AttributeError:
        return Border()


def _xlrd_fill_to_openpyxl(xf, book) -> PatternFill:
    """Convert xlrd XF background to openpyxl PatternFill."""
    try:
        bg = xf.background
        pattern_map = {
            0: "none", 1: "solid", 2: "mediumGray", 3: "darkGray",
            4: "lightGray", 5: "darkHorizontal",
        }
        pattern = pattern_map.get(bg.pattern_type, "none")
        if pattern == "none":
            return PatternFill()
        fg_color = "FFFFFF"
        if (
            bg.pattern_colour_index is not None
            and bg.pattern_colour_index != 0x7FFF
        ):
            colour_map = book.colour_map.get(bg.pattern_colour_index)
            if colour_map:
                fg_color = "{:02X}{:02X}{:02X}".format(*colour_map)
        return PatternFill(patternType=pattern, fgColor=fg_color)
    except AttributeError:
        return PatternFill()


def _xlrd_number_format(xf, book) -> str:
    """Get the number format string from xlrd XF."""
    try:
        fmt_key = xf.format_key
        fmt_map = book.format_map
        if fmt_key in fmt_map:
            return fmt_map[fmt_key].format_str
    except (AttributeError, KeyError):
        pass
    return "General"

# -------------------------------------------------------------------------
# MAIN CONVERTER
# -------------------------------------------------------------------------

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

    Method 1 (Absolute Primary - Windows): Microsoft Excel natively (win32com)
    Method 2 (Primary - Mac/Linux): LibreOffice `soffice --headless`
    Method 3 (Fallback): xlrd -> openpyxl (~95% visual formatting)
    """
    logger.info(f"Converting legacy .xls: {os.path.basename(filepath)}")
    filepath = os.path.abspath(filepath)
    dir_name = os.path.dirname(filepath)
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    # We output to a temp file starting with a dot
    temp_path = os.path.join(dir_name, f".{base_name}_converted.xlsx")

    # ── Method 1: PERFECT MICROSOFT EXCEL CONVERSION (WINDOWS ONLY) ───
    if os.name == "nt":
        try:
            import win32com.client  # type: ignore
            logger.info("Using Native Microsoft Excel Engine (win32com) for 100% fidelity.")
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            try:
                wb = excel.Workbooks.Open(filepath)
                # FileFormat = 51 is purely .xlsx
                wb.SaveAs(os.path.abspath(temp_path), FileFormat=51)
                wb.Close(False)
            finally:
                excel.Quit()
            if os.path.exists(temp_path):
                logger.info(f"MS Excel native conversion complete: {temp_path}")
                return temp_path
        except Exception as e:
            logger.warning(f"Native Excel conversion failed, falling back to LibreOffice... {e}")

    # ── Method 2: PERFECT LIBREOFFICE CONVERSION (MAC/LINUX) ─────────
    soffice_path = _get_soffice_path()
    if soffice_path:
        logger.info("Using LibreOffice Native Engine for 100% formatting fidelity.")
        try:
            # soffice --headless --convert-to xlsx --outdir <dir> <file>
            # It outputs to <outdir>/<base_name>.xlsx
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
                logger.info(f"Native conversion complete: {temp_path}")
                return temp_path
        except Exception as e:
            logger.warning(f"LibreOffice conversion failed, falling back to xlrd... {e}")

    # ── Method 2: XLRD FRAMEWORK FALLBACK ────────────────────────────
    logger.warning("LibreOffice not found or failed. Using xlrd XF format fallback (~95% fidelity).")

    # formatting_info=True enables reading XF records for styles
    wb_xls = xlrd.open_workbook(filepath, formatting_info=True)
    wb_xlsx = openpyxl.Workbook()
    wb_xlsx.remove(wb_xlsx.active)

    for sheet_name in wb_xls.sheet_names():
        ws_xls = wb_xls.sheet_by_name(sheet_name)
        ws_xlsx = wb_xlsx.create_sheet(title=sheet_name)

        # ── Copy merged cells ────────────────────────────────────
        merged_skip = set()
        for (rlo, rhi, clo, chi) in ws_xls.merged_cells:
            ws_xlsx.merge_cells(
                start_row=rlo + 1, start_column=clo + 1,
                end_row=rhi, end_column=chi,
            )
            for r in range(rlo, rhi):
                for c in range(clo, chi):
                    if r != rlo or c != clo:
                        merged_skip.add((r, c))

        # ── Copy column widths ───────────────────────────────────
        for col_idx in range(ws_xls.ncols):
            try:
                col_width = ws_xls.colinfo_map.get(col_idx)
                if col_width:
                    width = col_width.width / 256
                    col_letter = get_column_letter(col_idx + 1)
                    ws_xlsx.column_dimensions[col_letter].width = width
            except (AttributeError, KeyError):
                pass

        # ── Copy row heights ─────────────────────────────────────
        for row_idx in range(ws_xls.nrows):
            try:
                row_info = ws_xls.rowinfo_map.get(row_idx)
                if row_info and row_info.height:
                    ws_xlsx.row_dimensions[row_idx + 1].height = (
                        row_info.height / 20
                    )
            except (AttributeError, KeyError):
                pass

        # ── Copy cell values + styles ────────────────────────────
        for row_idx in range(ws_xls.nrows):
            for col_idx in range(ws_xls.ncols):
                if (row_idx, col_idx) in merged_skip:
                    continue

                cell_type = ws_xls.cell_type(row_idx, col_idx)
                cell_value = ws_xls.cell_value(row_idx, col_idx)

                if cell_type == xlrd.XL_CELL_DATE:
                    try:
                        cell_value = xlrd.xldate_as_datetime(
                            cell_value, wb_xls.datemode
                        )
                    except Exception as e:
                        logger.debug(
                            "Date conversion skipped for cell: %s", e
                        )

                cell = ws_xlsx.cell(
                    row=row_idx + 1,
                    column=col_idx + 1,
                    value=cell_value,
                )

                try:
                    xf_index = ws_xls.cell_xf_index(row_idx, col_idx)
                    xf = wb_xls.xf_list[xf_index]
                    cell.font = _xlrd_font_to_openpyxl(xf, wb_xls)
                    cell.alignment = _xlrd_alignment_to_openpyxl(xf)
                    cell.border = _xlrd_borders_to_openpyxl(xf, wb_xls)
                    cell.fill = _xlrd_fill_to_openpyxl(xf, wb_xls)
                    cell.number_format = _xlrd_number_format(xf, wb_xls)
                except (IndexError, AttributeError):
                    pass

    wb_xlsx.save(temp_path)
    logger.info(f"Fallback conversion complete: {temp_path}")
    return temp_path
