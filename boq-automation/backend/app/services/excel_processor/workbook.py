"""Read-only workbook inspection used by upload validation and previews."""
import io
import re
import warnings
import zipfile
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", module="openpyxl")

BOQ_REQUIRED = ["Unique No", "Item Brief", "Description", "Sales Item", "Cost Sheet Type",
                "Billable Qty", "Qty UOM", "ClassName", "Item No."]
ITEM_MASTER_REQUIRED = ["Item No", "Item Brief", "Cost Sheet Type", "QuantityUom", "Cost"]


class WorkbookError(Exception):
    pass


def open_values(path: str | Path):
    try:
        return openpyxl.load_workbook(path, data_only=True, read_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, TypeError) as e:
        raise WorkbookError(f"Not a readable Excel workbook: {e}") from e


MINIMAL_STYLES = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                  b'<fonts count="1"><font/></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
                  b'<borders count="1"><border/></borders>'
                  b'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                  b'<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
                  b'</styleSheet>')


def _repaired_copy(path: str | Path) -> io.BytesIO:
    """An in-memory copy of a workbook with the parts openpyxl most often chokes on made safe:
    the stylesheet is replaced by a minimal one and defined names are dropped. Cell values are
    untouched, and the file on disk is never written."""
    out = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/styles.xml":
                data = MINIMAL_STYLES
            elif info.filename == "xl/workbook.xml":
                data = re.sub(rb"<definedNames>.*?</definedNames>", b"", data, flags=re.S)
            dst.writestr(info, data)
    out.seek(0)
    return out


def open_values_tolerant(path: str | Path):
    """open_values, falling back to a repaired in-memory copy for workbooks whose styles or
    defined names are malformed (Excel opens them; openpyxl refuses). Returns (workbook, note)."""
    try:
        return open_values(path), ""
    except WorkbookError as first:
        if isinstance(first.__cause__, (zipfile.BadZipFile, OSError)):
            raise
        try:
            wb = openpyxl.load_workbook(_repaired_copy(path), data_only=True, read_only=True)
        except Exception as e:  # still unreadable: report the original reason
            raise first from e
        return wb, ("Formatting or named ranges in this workbook could not be read; values were read from a "
                    "repaired in-memory copy (the original file is unchanged).")


def find_header(ws, required: list[str], scan: int = 5) -> tuple[int, list[str]] | None:
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=scan, values_only=True), start=1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if all(r in cells for r in required):
            return i, cells
    return None


def inspect_boq(path: str | Path) -> dict:
    """Detect the layout of every worksheet and pick the one most likely to be the BOQ."""
    from app.services.boq_parser.layout import detect_layout

    wb = open_values(path)
    sheets = []
    for ws in wb.worksheets:
        layout = detect_layout(ws)
        # Google Sheets and some other tools omit the size record, so max_row can be None:
        # then count the rows that actually hold something.
        rows = ws.max_row or sum(1 for r in ws.iter_rows(values_only=True) if any(v not in (None, "") for v in r))
        sheets.append({"name": ws.title, "state": ws.sheet_state, "is_sales_bom": layout["format"] == "sales_bom",
                       "header_row": layout["header_row"], "rows": rows, "layout": layout})
    wb.close()
    with_data = [s for s in sheets if s["rows"] > 1]
    if not with_data:
        raise WorkbookError("The workbook has no worksheet with data.")
    rank = {"high": 0, "low": 1, "none": 2}

    def preference(s):
        lay = s["layout"]
        return (lay["format"] != "sales_bom", s["name"].lower() != "sales bom", s["state"] != "visible",
                rank[lay["confidence"]], -s["rows"])
    preferred = min(with_data, key=preference)
    with zipfile.ZipFile(path) as z:
        has_macros = "xl/vbaProject.bin" in z.namelist()
    return {"sheets": sheets, "selected_sheet": preferred["name"], "has_macros": has_macros}


def preview_sheet(path: str | Path, sheet: str, limit: int = 40) -> dict:
    wb = open_values(path)
    if sheet not in wb.sheetnames:
        wb.close()
        raise WorkbookError(f"Worksheet not found: {sheet}")
    rows = [["" if c is None else str(c) for c in row]
            for row in wb[sheet].iter_rows(min_row=1, max_row=limit + 2, values_only=True)]
    wb.close()
    return {"sheet": sheet, "rows": rows}
