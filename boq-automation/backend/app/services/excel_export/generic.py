"""Export for BOQs that are not in the Sales BOM template.

Each approved component becomes a new row directly below its BOQ item (below the item's
specification rows and any components it already has). The row states:

  description column       the component's Item Brief, from the Item Master
  Component Item No.       the exact Item Master number
  Cost Sheet Type          the reviewed classification (Highside Supply / Installation)

Existing columns with those roles are used; only missing ones are added, to the right of the
data. Every new row is yellow across the whole row. Nothing else is restyled or moved except
by the insertion itself: formulas, totals, merged cells, validations, names, charts and
drawings are shifted exactly as Excel shifts them on "Insert Row" (see excel_processor.shift).
"""
import re
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl.utils import column_index_from_string, get_column_letter

from app.services.boq_parser.parser import parse_boq
from app.services.excel_processor.ooxml import (
    CELL_RE, OoxmlError, RawRow, SheetDoc, StyleSheet, XlsxPackage, add_override_format, cell_styles,
)
from app.services.excel_processor.shift import insert_rows

INSERTED_FILL = "FFFFFF00"
ITEM_HEADER = "Component Item No."
TYPE_HEADER = "Cost Sheet Type"
WIDTHS = {ITEM_HEADER: 18, TYPE_HEADER: 22}
HAS_VALUE_RE = re.compile(r"<(?:v|f|is)[\s>/]")
MERGE_RE = re.compile(r'<mergeCell ref="[A-Z]+\d+:([A-Z]+)\d+"')
COL_RE = re.compile(r"<col\b([^>]*?)/>")
ROW_ATTRS_DROPPED = re.compile(r'\s+(?:spans|ht|customHeight|hidden|outlineLevel|collapsed|thickBot|thickTop)="[^"]*"')


def _cell(col: str, row: int, value, style: str) -> str:
    """Text as an inline string: works whether or not the workbook has a shared-strings part."""
    s = f' s="{style}"' if style and style != "0" else ""
    if value is None or value == "":
        return f'<c r="{col}{row}"{s}/>'
    return f'<c r="{col}{row}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _set_cells(row: RawRow, values: dict[str, object], styles: dict[str, str]) -> RawRow:
    """Put cells into an existing row, replacing any already in those columns, in column order."""
    cells = [(m.group(1), m.group(0)) for m in CELL_RE.finditer(row.body or "") if m.group(1) not in values]
    cells += [(c, _cell(c, row.number, v, styles.get(c, ""))) for c, v in values.items()]
    cells.sort(key=lambda x: column_index_from_string(x[0]))
    attrs = re.sub(r'\s+spans="[^"]*"', "", row.attrs)  # spans is only a hint; drop it rather than keep it wrong
    return RawRow(row.number, attrs, "".join(x for _, x in cells))


def _last_column(doc: SheetDoc) -> int:
    """Last column holding a value, formula or merged range.

    Blank cells that only carry formatting are ignored: Google Sheets exports style every cell
    up to column Z, and counting those put added columns far off to the right.
    """
    used = [column_index_from_string(m.group(1)) for r in doc.rows for m in CELL_RE.finditer(r.body or "")
            if m.group(4) and HAS_VALUE_RE.search(m.group(4))]
    used += [column_index_from_string(c) for c in MERGE_RE.findall(doc.tail)]
    return max(used, default=1)


def _bound(attrs: str, name: str) -> int:
    return int(re.search(rf'{name}="(\d+)"', attrs).group(1))


def _with_bound(attrs: str, name: str, value: int) -> str:
    return re.sub(rf'{name}="\d+"', f'{name}="{value}"', attrs)


def _set_widths(head: str, widths: dict[int, int]) -> str:
    """Give added columns readable widths, splitting any <col> range that covers them."""
    if not widths:
        return head
    added = [f' min="{c}" max="{c}" width="{w}" customWidth="1"' for c, w in sorted(widths.items())]
    cols = re.search(r"<cols>(.*?)</cols>", head, re.S)
    if not cols:
        at = head.index("<sheetData")
        return head[:at] + "<cols>" + "".join(f"<col{a}/>" for a in added) + "</cols>" + head[at:]
    kept = []
    for attrs in COL_RE.findall(cols.group(1)):
        lo, hi = _bound(attrs, "min"), _bound(attrs, "max")
        inside = [c for c in widths if lo <= c <= hi]
        if not inside:
            kept.append(attrs)
            continue
        start = lo
        for c in sorted(inside):
            if c > start:
                kept.append(_with_bound(_with_bound(attrs, "min", start), "max", c - 1))
            start = c + 1
        if start <= hi:
            kept.append(_with_bound(_with_bound(attrs, "min", start), "max", hi))
    ordered = sorted(kept + added, key=lambda a: _bound(a, "min"))
    return head[:cols.start()] + "<cols>" + "".join(f"<col{a}/>" for a in ordered) + "</cols>" + head[cols.end():]


def export_generic(source: str | Path, sheet: str, plans: list, destination: str | Path, layout: dict) -> dict:
    parsed = parse_boq(source, sheet, layout)
    lines = {l.row.row: l for l in parsed.lines}
    header_row = int(layout["header_row"])
    cols = layout.get("columns") or {}
    pkg = XlsxPackage(source)
    part = pkg.sheet_part(sheet)
    doc = SheetDoc.parse(pkg.text(part))

    by_parent: dict[int, list] = {}
    for p in plans:
        if p.parent_row not in lines:
            raise OoxmlError(f"Row {p.parent_row} is not a BOQ item")
        by_parent.setdefault(p.parent_row, []).append(p)

    # Use the sheet's own component columns when it has them; add only what is missing.
    desc_col = get_column_letter(int(cols["description"]) + 1)
    next_free = _last_column(doc) + 1
    added_cols: dict[str, str] = {}
    letters = {}
    for role, header in (("mapped_item_no", ITEM_HEADER), ("cost_sheet_type", TYPE_HEADER)):
        if cols.get(role) is not None:
            letters[role] = get_column_letter(int(cols[role]) + 1)
        elif plans:
            letters[role] = get_column_letter(next_free)
            added_cols[letters[role]] = header
            next_free += 1
    last = max([_last_column(doc)] + [column_index_from_string(c) for c in letters.values()])

    stylesheet = StyleSheet(pkg)
    rows = doc.row_map()
    filled: dict[str, str] = {}

    def yellow(style: str) -> str:
        if style not in filled:
            filled[style] = stylesheet.filled(style, INSERTED_FILL)
        return filled[style]

    inserts: dict[int, list[RawRow]] = {}
    expected_by_line: list[tuple[int, object]] = []
    for parent_row, group in sorted(by_parent.items()):
        line = lines[parent_row]
        src = rows.get(parent_row)
        base = cell_styles(src) if src else {}
        fallback = base.get(desc_col, "0")
        styles = {get_column_letter(i): yellow(base.get(get_column_letter(i), fallback)) for i in range(1, last + 1)}
        attrs = ROW_ATTRS_DROPPED.sub("", src.attrs) if src else ""
        for plan in group:
            values = {desc_col: plan.item_brief, letters["mapped_item_no"]: plan.item_no,
                      letters["cost_sheet_type"]: plan.cost_sheet_type}

            def body(n, values=values, styles=styles):
                cells = sorted(set(styles) | set(values), key=column_index_from_string)
                return "".join(_cell(c, n, values.get(c), styles.get(c, "")) for c in cells)
            inserts.setdefault(line.last_row, []).append(RawRow(0, attrs, body))
            expected_by_line.append((line.last_row, plan))

    doc, mapping, added, touched = insert_rows(pkg, sheet, inserts)

    if added_cols:  # header cells for the columns this export adds, styled like the sheet's header
        header = next((r for r in doc.rows if r.number == mapping.get(header_row, header_row)), None)
        hstyle = cell_styles(header).get(desc_col, "") if header else ""
        if header is None:
            header = RawRow(mapping.get(header_row, header_row), "", "")
            doc.rows.append(header)
            doc.rows.sort(key=lambda r: r.number)
        updated = _set_cells(header, added_cols, {c: hstyle for c in added_cols})
        doc.rows = [updated if r.number == updated.number else r for r in doc.rows]
        doc.head = _set_widths(doc.head, {column_index_from_string(c): WIDTHS[h] for c, h in added_cols.items()})
    if added:
        # Outranks any conditional format of the template on these rows, and only these rows.
        last_letter = get_column_letter(last)
        runs: list[list[int]] = []
        for n in sorted(added):
            if runs and n == runs[-1][1] + 1:
                runs[-1][1] = n
            else:
                runs.append([n, n])
        add_override_format(doc, " ".join(f"A{a}:{last_letter}{b}" for a, b in runs), stylesheet.fill_dxf(INSERTED_FILL))
    last_row = max((r.number for r in doc.rows), default=1)
    doc.head = re.sub(r'<dimension ref="([A-Z]+\d+)(?::[A-Z]+\d+)?"/>',
                      lambda m: f'<dimension ref="{m.group(1)}:{get_column_letter(last)}{last_row}"/>', doc.head)
    pkg.set_text(part, doc.serialize())
    stylesheet.flush()
    if added:
        pkg.drop_calc_chain()
        pkg.force_full_recalc()
    pkg.save(destination)

    expected, cursor = [], {}
    for after, plan in expected_by_line:
        cursor[after] = cursor.get(after, 0) + 1
        expected.append({"row": mapping[after] + cursor[after], "parent_row": mapping.get(plan.parent_row),
                         "item_no": plan.item_no, "item_brief": plan.item_brief,
                         "cost_sheet_type": plan.cost_sheet_type, "class_name": plan.class_name})
    return {"format": "generic", "expected": expected, "sheet_part": part, "header_row": header_row,
            "row_mapping": mapping, "added_rows": added, "added_columns": added_cols,
            "columns": {"description": desc_col, **letters}, "touched_parts": sorted(touched),
            "source_rows": len(parsed.rows), "fill_columns": last,
            "blocks": [{"parent_row": pr, "block_end": lines[pr].last_row, "count": len(g)}
                       for pr, g in sorted(by_parent.items())],
            "line_rows": sorted(lines)}
