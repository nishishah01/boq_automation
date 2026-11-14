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

import re

from app.services.boq_parser.eligibility import quantity_state
from app.services.boq_parser.layout import detect_layout, validate_layout
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
    extra: str = ""  # specification rows under a line in a generic BOQ ("Make: Daikin")
    qty_raw: str = ""  # the quantity cell exactly as written, for messages
    qty_state: str = "valid"  # valid | zero | missing | invalid (see eligibility.py)

    @property
    def text(self) -> str:
        """Description is authoritative; Item Brief is the fallback when it is blank."""
        base = (self.description or self.item_brief).strip()
        return f"{base}; {self.extra}" if self.extra else base


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
    layout: dict | None = None


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


def parse_boq(path: str | Path, sheet: str = "Sales BOM", layout: dict | None = None) -> ParsedBoq:
    """Parse one worksheet. Without a layout, the format is detected from the sheet itself."""
    wb = open_values(path)
    if sheet not in wb.sheetnames:
        wb.close()
        raise WorkbookError(f"Worksheet not found: {sheet}")
    ws = wb[sheet]
    if layout is None:
        layout = detect_layout(ws)
    if layout.get("format") == "generic":
        try:
            return parse_generic(ws, layout)
        finally:
            wb.close()
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
            qty_raw=_s(at(v, "Billable Qty")), qty_state=quantity_state(at(v, "Billable Qty"))[0],
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
    return ParsedBoq(sheet, header_row, rows, lines, warnings, {"format": "sales_bom", "sheet": sheet,
                                                                "header_row": header_row})


# ---------------- any other BOQ layout ----------------

TOTAL_RE = re.compile(r"^\s*(grand\s+|sub[\s-]*|page\s+)?total\b|carried\s+(forward|over)|brought\s+forward|"
                      r"^\s*[cb]\s*/\s*f\b", re.I)
SPEC_RE = re.compile(r"^\s*([-•*>]|(make|model|brand|capacity|size|rating|type|note|nb|remarks?|refrigerant|"
                     r"power\s+supply|airflow|warranty)\b\s*[:\-])", re.I)
DOTTED_RE = re.compile(r"^\d+(\.\d+)*\.?$")


def _num(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        t = v.replace(",", "").strip()
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _serial_level(serial: str) -> int | None:
    """'2' -> 1, '2.3' -> 2, '2.3.1' -> 3; letters and roman numerals have no level of their own."""
    s = serial.strip().rstrip(".")
    if not s or not DOTTED_RE.match(s):
        return None
    while s.endswith(".0") and s.count(".") > 0:  # "2.0" is section 2
        s = s[:-2]
    return s.count(".") + 1


def _is_title(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(text) <= 80 and len(letters) >= 3 and all(c.isupper() for c in letters)


def component_class(item_no: str) -> str:
    """Item Master convention: IN- numbers are installation items, everything else a component."""
    return "Installation" if item_no.upper().startswith("IN-") else "Component"


def parse_generic(ws, layout: dict, require_unit_qty: bool = True) -> ParsedBoq:
    """Lines are rows with a description and a quantity (or unit/rate); rows with a description
    and no quantity are headings that give the following lines their context, or, directly
    under a line, specifications of that line. A row with an Item Master number in the
    component column (written by this app's export, or present in a historical BOQ) is a
    component of the line above it. Nothing is assumed about row positions.

    require_unit_qty=False reads historical BOQs, where only the hierarchy matters."""
    problems = validate_layout(layout, require_unit_qty)
    if problems:
        raise WorkbookError(" ".join(problems))
    cols = {k: v for k, v in (layout.get("columns") or {}).items() if v is not None}
    header_row = int(layout["header_row"])

    def at(values, role):
        i = cols.get(role)
        return values[i] if i is not None and i < len(values) else None

    rows: list[BoqRow] = []
    lines: list[SalesLine] = []
    warnings: list[str] = []
    stack: list[tuple[int, BoqRow, bool]] = []  # open headings: (level, row, numbered or a TITLE)
    prev_kind = ""
    last_line: SalesLine | None = None
    has_qty = "quantity" in cols
    for r, v in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        desc = _s(at(v, "description"))
        mapped = _s(at(v, "mapped_item_no"))
        below = layout.get("components_below") or (
            quantity_state(at(v, "quantity"))[0] == "missing" and not _s(at(v, "unit")))
        if mapped and last_line is not None and below:
            # A component row under its BOQ item: one Item Master number, its brief, its type.
            for no in [x.strip() for x in mapped.splitlines() if x.strip()]:
                last_line.children.append(BoqRow(
                    r, f"{last_line.row.unique_no}.{len(last_line.children) + 1}", last_line.row.unique_no, "",
                    desc, "", "N", _s(at(v, "cost_sheet_type")), 0, "", 0, component_class(no), no))
            last_line.last_row = r
            prev_kind = "component"
            continue
        if not desc or desc.replace(".", "").isdigit():
            continue
        if TOTAL_RE.search(desc):
            prev_kind = "total"
            continue
        serial = _s(at(v, "serial"))
        qty_cell = at(v, "quantity")
        qty_state, _ = quantity_state(qty_cell)
        qty = _num(qty_cell)
        unit = _s(at(v, "unit"))
        rate, amount = _num(at(v, "rate")), _num(at(v, "amount"))
        # Headings never carry a unit, quantity or price; an item does even when its quantity is 0
        # (not required in this revision of the BOQ, but still a line the client prices).
        # A row with any of these is a BOQ item, even if its unit or quantity is missing: it is then
        # listed as skipped, not taken for a heading. Headings have none of them.
        is_line = bool(qty_state != "missing" or unit or (rate and rate > 0) or (amount and amount > 0))
        row = BoqRow(row=r, unique_no=serial or f"R{r}", parent_no="", cust_ref=serial, item_brief="",
                     description=desc, sales_item="Y" if is_line else "N",
                     cost_sheet_type=_s(at(v, "cost_sheet_type")), billable_qty=qty or 0.0, uom=unit, cost=0.0,
                     class_name="", item_no="", qty_raw=_s(qty_cell), qty_state=qty_state)
        rows.append(row)
        if is_line:
            line = SalesLine(row=row, ancestors=[a for _, a, _ in reversed(stack)], last_row=r)
            # Older exports listed the components in cells on the item's own row.
            for no in [x.strip() for x in mapped.splitlines() if x.strip()]:
                line.children.append(BoqRow(r, f"{row.unique_no}.*", row.unique_no, "", "", "", "N",
                                            _s(at(v, "cost_sheet_type")).splitlines()[0] if _s(at(v, "cost_sheet_type")) else "",
                                            0, unit, 0, component_class(no), no))
            lines.append(line)
            last_line, prev_kind = line, "line"
            continue
        if last_line is not None and prev_kind in ("line", "spec") and SPEC_RE.match(desc):
            last_line.row.extra = f"{last_line.row.extra}; {desc}".strip("; ")
            last_line.last_row = r  # components go below the item's specification rows
            prev_kind = "spec"
            continue
        # A heading: its depth comes from the serial number when it has one, else from the order.
        level = _serial_level(serial)
        if level is None:
            if _is_title(desc):
                level = 0
            elif prev_kind == "heading" and stack and not stack[-1][2]:
                level = stack[-1][0]  # paragraph after paragraph: the next note, not a deeper one
            elif prev_kind == "heading" and stack:
                level = stack[-1][0] + 1
            else:
                level = stack[-1][0] if stack else 1
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, row, _serial_level(serial) is not None or _is_title(desc)))
        prev_kind = "heading"

    if not lines:
        unit_hint = "quantity" if has_qty else "unit, rate or amount"
        warnings.append(f"No BOQ items were found: no row below the headings has a {unit_hint} value.")
    return ParsedBoq(ws.title, header_row, rows, lines, warnings, layout)
