"""Parse a Sales BOM worksheet into its real hierarchy.

Observed structure (BOQ_IMPORT_REST_PROD_V15):
  Section     G=N, H=Section, no ClassName        groups lines; may carry the scope text
  Sales line  G=Y                                 the billable "parent description"
  Child       ClassName Component|Installation    Item Master selection under a sales line

A sales line's meaning often sits in its ancestors: section "Supply & Installation of PVC
Drain Piping with Insulation" > sales line "40mm". So each line carries its ancestor chain.
"""
from dataclasses import dataclass, field
from pathlib import Path

from app.services.excel_processor.workbook import BOQ_REQUIRED, WorkbookError, find_header, open_values

CHILD_CLASSES = {"Component", "Installation"}


@dataclass
class BoqRow:
    row: int
    unique_no: str
    parent_no: str
    cust_ref: str
    item_brief: str
    description: str
    sales_item: str
    cost_sheet_type: str
    billable_qty: float
    uom: str
    cost: float
    class_name: str
    item_no: str

    @property
    def text(self) -> str:
        """Description is authoritative; Item Brief is the fallback when it is blank."""
        return (self.description or self.item_brief).strip()


@dataclass
class SalesLine:
    row: BoqRow
    ancestors: list[BoqRow] = field(default_factory=list)  # nearest first, project root excluded
    children: list[BoqRow] = field(default_factory=list)
    last_row: int = 0  # last sheet row belonging to this line

    @property
    def context(self) -> list[str]:
        return [a.text for a in self.ancestors]

    @property
    def is_mapped(self) -> bool:
        return bool(self.children)


@dataclass
class ParsedBoq:
    sheet: str
    header_row: int
    rows: list[BoqRow]
    lines: list[SalesLine]
    warnings: list[str]


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def derive_parent(unique_no: str) -> str:
    return unique_no.rsplit(".", 1)[0] if "." in unique_no else ""


def parse_boq(path: str | Path, sheet: str = "Sales BOM") -> ParsedBoq:
    wb = open_values(path)
    if sheet not in wb.sheetnames:
        wb.close()
        raise WorkbookError(f"Worksheet not found: {sheet}")
    ws = wb[sheet]
    found = find_header(ws, BOQ_REQUIRED)
    if not found:
        wb.close()
        raise WorkbookError(f"{sheet}: required columns are missing")
    header_row, header = found
    col = {name: i for i, name in enumerate(header) if name}

    def at(values, name):
        i = col.get(name)
        return values[i] if i is not None and i < len(values) else None

    rows: list[BoqRow] = []
    warnings: list[str] = []
    for r, v in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        uno = _s(at(v, "Unique No"))
        if not uno:
            if _s(at(v, "Item Brief")):
                warnings.append(f"Row {r}: Item Brief present but Unique No is blank; row ignored")
            continue
        rows.append(BoqRow(
            row=r, unique_no=uno,
            parent_no=_s(at(v, "ParentUniqueNo")) or derive_parent(uno),
            cust_ref=_s(at(v, "Cust Ref No.")), item_brief=_s(at(v, "Item Brief")),
            description=_s(at(v, "Description")), sales_item=_s(at(v, "Sales Item")).upper(),
            cost_sheet_type=_s(at(v, "Cost Sheet Type")), billable_qty=_f(at(v, "Billable Qty")),
            uom=_s(at(v, "Qty UOM")), cost=_f(at(v, "Cost")),
            class_name=_s(at(v, "ClassName")), item_no=_s(at(v, "Item No.")),
        ))
    wb.close()

    by_no: dict[str, BoqRow] = {}
    for x in rows:
        if x.unique_no in by_no:
            warnings.append(f"Row {x.row}: duplicate Unique No {x.unique_no}")
        by_no.setdefault(x.unique_no, x)

    def chain(x: BoqRow) -> list[BoqRow]:
        out, seen, cur = [], {x.unique_no}, x.parent_no
        while cur and cur in by_no and cur not in seen:
            seen.add(cur)
            out.append(by_no[cur])
            cur = by_no[cur].parent_no
        return out

    lines: list[SalesLine] = []
    line_by_no: dict[str, SalesLine] = {}
    for x in rows:
        if x.sales_item == "Y":
            # The level-1 row is the project/site title, not a scope description.
            anc = [a for a in chain(x) if a.sales_item != "Y" and "." in a.unique_no]
            line = SalesLine(row=x, ancestors=anc, last_row=x.row)
            lines.append(line)
            line_by_no[x.unique_no] = line

    for x in rows:
        if x.class_name in CHILD_CLASSES or x.item_no:
            owner = next((line_by_no[a.unique_no] for a in chain(x) if a.unique_no in line_by_no), None)
            if owner is None:
                warnings.append(f"Row {x.row}: component {x.item_no or x.item_brief} has no parent sales line")
                continue
            owner.children.append(x)
            owner.last_row = max(owner.last_row, x.row)
    return ParsedBoq(sheet, header_row, rows, lines, warnings)
