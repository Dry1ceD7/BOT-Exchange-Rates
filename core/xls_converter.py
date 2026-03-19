#!/usr/bin/env python3
"""
core/xls_converter.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.5) - Legacy .xls Conversion Pipeline
---------------------------------------------------------------------------
Separated from engine.py for SFFB compliance (<200 lines).
Converts legacy .xls files to .xlsx using xlrd + openpyxl.
"""

import logging
import os

import openpyxl
import xlrd

logger = logging.getLogger(__name__)


def convert_xls_to_xlsx(filepath: str) -> str:
    """
    Convert a legacy .xls file to .xlsx using xlrd + openpyxl.
    Returns the path to the new .xlsx file.
    Constraint: No pandas, no win32com — memory-safe, cross-platform.
    """
    logger.info(f"Converting legacy .xls: {os.path.basename(filepath)}")
    wb_xls = xlrd.open_workbook(filepath, formatting_info=False)
    wb_xlsx = openpyxl.Workbook()
    # Remove the default sheet created by openpyxl
    wb_xlsx.remove(wb_xlsx.active)

    for sheet_name in wb_xls.sheet_names():
        ws_xls = wb_xls.sheet_by_name(sheet_name)
        ws_xlsx = wb_xlsx.create_sheet(title=sheet_name)

        for row_idx in range(ws_xls.nrows):
            for col_idx in range(ws_xls.ncols):
                cell_type = ws_xls.cell_type(row_idx, col_idx)
                cell_value = ws_xls.cell_value(row_idx, col_idx)

                # Convert xlrd date floats to Python datetime objects
                if cell_type == xlrd.XL_CELL_DATE:
                    try:
                        cell_value = xlrd.xldate_as_datetime(
                            cell_value, wb_xls.datemode
                        )
                    except Exception as e:
                        logger.debug("Date conversion skipped for cell: %s", e)

                ws_xlsx.cell(
                    row=row_idx + 1,
                    column=col_idx + 1,
                    value=cell_value
                )

    # Save to a temp file in the same directory (preserves filesystem)
    dir_name = os.path.dirname(filepath)
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    temp_path = os.path.join(dir_name, f".{base_name}_converted.xlsx")
    wb_xlsx.save(temp_path)
    logger.info(f"Conversion complete: {temp_path}")
    return temp_path
