"""Insert approved components into a copy of the BOQ, editing the package XML directly.

Child-row conventions, all taken from the mapped rows of the real Sales BOM:
  A  parent Unique No + running number     B  parent Unique No
  D  Item Brief exactly as in the master   E  the parent's Description, verbatim
  G  N                                     H  the component's own master Cost Sheet Type
  I  parent Billable Qty                   J, K  0
  L, Q, R, S, T  the sheet's row formulas  M  parent Qty UOM (not the master UOM)
  N  master Cost                           O  0
  U  Component / Installation              V  Item No.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.boq_parser.parser import ParsedBoq, SalesLine, parse_boq
from app.services.excel_processor.ooxml import (
    Formula, OoxmlError, RawRow, SharedStrings, SheetDoc, XlsxPackage, assert_same_row_formulas,
    cell_styles, relocate_row, render_row_body,
)


@dataclass
class ChildPlan:
    parent_row: int  # the sales line's sheet row in the source workbook
    item_no: str
    item_brief: str
    cost_sheet_type: str
    class_name: str
    cost: float


def child_values(line_row, unique_no: str, plan: ChildPlan, row: int) -> dict:
    qty = line_row.billable_qty
    qty_v = int(qty) if float(qty).is_integer() else qty
    cost = plan.cost if plan.cost > 0 else 0
    return {
        "A": unique_no, "B": line_row.unique_no, "D": plan.item_brief, "E": line_row.description,
        "G": "N", "H": plan.cost_sheet_type, "I": qty_v, "J": 0, "K": 0,
        "L": Formula(f"IFERROR(J{row}+K{row},0)", 0), "M": line_row.uom, "N": cost, "O": 0,
        "Q": Formula(f"IFERROR((O{row}/N{row}-1)*100,0)", -100 if cost else 0),
        "R": Formula(f"IFERROR(N{row}*Q{row}/100,0)", -cost),
        "S": Formula(f"IFERROR(ROUND(N{row}*L{row},2),0)", 0),
        "T": Formula(f"IFERROR(ROUND(O{row}*I{row},2),0)", 0),
        "U": plan.class_name, "V": plan.item_no,
    }


def next_child_numbers(line: SalesLine, count: int) -> list[str]:
    prefix = line.row.unique_no + "."
    used = [int(c.unique_no[len(prefix):]) for c in line.children
            if c.unique_no.startswith(prefix) and c.unique_no[len(prefix):].isdigit()]
    start = max(used, default=0) + 1
    return [f"{prefix}{start + i}" for i in range(count)]


def _child_style_row(doc: SheetDoc, parsed: ParsedBoq) -> RawRow:
    """Style exemplar: an existing component row, else a sales line."""
    rows = doc.row_map()
    for r in parsed.rows:
        if r.class_name in ("Component", "Installation") and r.row in rows:
            return rows[r.row]
    if parsed.lines and parsed.lines[0].row.row in rows:
        return rows[parsed.lines[0].row.row]
    raise OoxmlError("Sales BOM has no row to copy styles from")


def insert_rows(pkg: XlsxPackage, sheet: str, parsed: ParsedBoq,
                inserts: dict[int, list[tuple[dict[str, object], RawRow | None]]],
                sst: SharedStrings, styles: dict[str, str]) -> dict[int, int]:
    """Insert rendered rows after given source rows. Returns source row -> output row."""
    part = pkg.sheet_part(sheet)
    doc = SheetDoc.parse(pkg.text(part))
    assert_same_row_formulas(doc)
    source_rows = doc.row_map()
    new_rows: list[RawRow] = []
    mapping: dict[int, int] = {}
    shift = 0
    for row in doc.rows:
        target = row.number + shift
        mapping[row.number] = target
        new_rows.append(relocate_row(doc, row, target))
        for values, attrs_from in inserts.get(row.number, []):
            shift += 1
            n = row.number + shift
            attrs = (attrs_from or source_rows[row.number]).attrs
            new_rows.append(RawRow(n, attrs, render_row_body(values(n) if callable(values) else values,
                                                             n, styles, sst)))
    doc.rows = new_rows
    doc.shared = {}
    if shift:
        _fix_ranges(doc, pkg, sheet, mapping)
    pkg.set_text(part, doc.serialize())
    return mapping


def _shift(row: int, mapping: dict[int, int]) -> int:
    if row in mapping:
        return mapping[row]
    below = [k for k in mapping if k < row]
    return row + (mapping[max(below)] - max(below) if below else 0)


def _fix_ranges(doc: SheetDoc, pkg: XlsxPackage, sheet: str, mapping: dict[int, int]) -> None:
    def bump_dimension(m: re.Match) -> str:
        return f'<dimension ref="{m.group(1)}{m.group(2)}:{m.group(3)}{_shift(int(m.group(4)), mapping)}"/>'
    doc.head = re.sub(r'<dimension ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"/>', bump_dimension, doc.head)

    def bump_filter(m: re.Match) -> str:
        return f'{m.group(1)}{m.group(2)}:{m.group(3)}{_shift(int(m.group(4)), mapping)}{m.group(5)}'
    doc.tail = re.sub(r'(<autoFilter ref="[A-Z]+)(\d+):([A-Z]+)(\d+)(")', bump_filter, doc.tail)

    # The hidden _FilterDatabase name must stay equal to the autoFilter range.
    quoted = re.escape(f"'{sheet}'!")

    def bump_name(m: re.Match) -> str:
        return f'{m.group(1)}${m.group(2)}${m.group(3)}:${m.group(4)}${_shift(int(m.group(5)), mapping)}'
    wb = pkg.text("xl/workbook.xml")
    pkg.set_text("xl/workbook.xml", re.sub(
        rf"(_FilterDatabase\"[^>]*>{quoted})\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)", bump_name, wb))


def export_boq(source: str | Path, sheet: str, plans: list[ChildPlan], destination: str | Path) -> dict:
    """Write destination; source is never touched. Returns what is needed for verification."""
    parsed = parse_boq(source, sheet)
    lines = {l.row.row: l for l in parsed.lines}
    pkg = XlsxPackage(source)
    part = pkg.sheet_part(sheet)
    doc = SheetDoc.parse(pkg.text(part))
    exemplar = _child_style_row(doc, parsed)
    styles = cell_styles(exemplar)
    sst = SharedStrings(pkg)

    by_parent: dict[int, list[ChildPlan]] = {}
    for p in plans:
        by_parent.setdefault(p.parent_row, []).append(p)

    inserts: dict[int, list] = {}
    expected = []
    for parent_row, group in by_parent.items():
        line = lines.get(parent_row)
        if line is None:
            raise OoxmlError(f"Row {parent_row} is not a sales line")
        numbers = next_child_numbers(line, len(group))
        parent_raw = doc.row_map()[parent_row]
        for number, plan in zip(numbers, group):
            def make(n, line_row=line.row, u=number, pl=plan):
                return child_values(line_row, u, pl, n)
            inserts.setdefault(line.last_row, []).append((make, parent_raw))
            expected.append({"unique_no": number, "parent_no": line.row.unique_no, "item_no": plan.item_no,
                             "item_brief": plan.item_brief, "cost_sheet_type": plan.cost_sheet_type,
                             "class_name": plan.class_name, "description": line.row.description,
                             "uom": line.row.uom, "qty": line.row.billable_qty})

    mapping = insert_rows(pkg, sheet, parsed, inserts, sst, styles)
    sst.flush()
    pkg.drop_calc_chain()
    pkg.force_full_recalc()
    pkg.save(destination)
    return {"expected": expected, "row_mapping": mapping, "sheet_part": part,
            "source_rows": len(parsed.rows)}
