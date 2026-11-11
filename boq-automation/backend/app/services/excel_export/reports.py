"""Mapping and unmatched reports (new workbooks; the BOQ template is not involved)."""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


def _write(path: Path, title: str, headers: list[str], rows: list[list]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F2937")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for i, h in enumerate(headers, start=1):
        width = max(len(h) + 2, 14)
        width = min(60, max([width] + [min(60, len(str(r[i - 1] or ""))) for r in rows[:300]]))
        ws.column_dimensions[ws.cell(1, i).column_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def mapping_report(path: Path, rows: list[dict]) -> None:
    headers = ["BOQ Row", "Unique No", "Parent Description", "Sections", "Item No.", "Item Brief", "Cost Sheet Type",
               "ClassName", "Confidence", "Selection", "Status", "Source", "Reason", "Flags"]
    keys = ["row", "unique_no", "description", "context", "item_no", "item_brief", "cost_sheet_type", "class_name",
            "confidence", "selection_type", "status", "source", "reason", "flags"]
    _write(path, "Component Mapping", headers, [[r.get(k) for k in keys] for r in rows])


def unmatched_report(path: Path, rows: list[dict]) -> None:
    headers = ["BOQ Row", "Unique No", "Parent Description", "Sections", "Cost Sheet Type", "Status", "Gap"]
    keys = ["row", "unique_no", "description", "context", "cost_sheet_type", "status", "gap"]
    _write(path, "Unmatched", headers, [[r.get(k) for k in keys] for r in rows])
