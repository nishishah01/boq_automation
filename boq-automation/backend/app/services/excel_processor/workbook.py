"""Read-only workbook inspection used by upload validation and previews."""
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
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as e:
        raise WorkbookError(f"Not a readable Excel workbook: {e}") from e


def find_header(ws, required: list[str], scan: int = 5) -> tuple[int, list[str]] | None:
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=scan, values_only=True), start=1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if all(r in cells for r in required):
            return i, cells
    return None


def inspect_boq(path: str | Path) -> dict:
    wb = open_values(path)
    sheets = []
    for ws in wb.worksheets:
        h = find_header(ws, BOQ_REQUIRED)
        sheets.append({"name": ws.title, "state": ws.sheet_state, "is_sales_bom": h is not None,
                       "header_row": h[0] if h else None})
    wb.close()
    candidates = [s for s in sheets if s["is_sales_bom"]]
    if not candidates:
        raise WorkbookError("No worksheet has the Sales BOM columns: " + ", ".join(BOQ_REQUIRED))
    preferred = next((s for s in candidates if s["name"].lower() == "sales bom"), candidates[0])
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
