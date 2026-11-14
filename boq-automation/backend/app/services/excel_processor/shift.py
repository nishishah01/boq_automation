"""Insert rows into one worksheet of an OOXML package the way Excel's own "Insert Row" does.

Every reference to a row at or below an insertion point moves down: cell formulas on this
sheet and on every other sheet, defined names (print areas, filter ranges), merged cells,
conditional formats, data validations, hyperlinks, the auto-filter, tables, comments,
drawing anchors and chart series. A range that spans an insertion point grows, so totals
such as SUM(F5:F40) keep covering the same items. Nothing else in the package changes.
"""
from __future__ import annotations

import posixpath
import re
from bisect import bisect_left
from xml.sax.saxutils import escape

from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.formula.translate import Translator

from .ooxml import CELL_RE, SHARED_F_RE, RawRow, SheetDoc, XlsxPackage, _unesc

CELL_REF = re.compile(r"(\$?[A-Z]{1,3})?(\$?)(\d+)")
ANY_F_RE = re.compile(r"<f(\s[^>]*)?>(.*?)</f>", re.S)


class RowShift:
    """Old row number -> new row number, for rows inserted after given rows."""

    def __init__(self, inserted_after: dict[int, int]):
        self.points = sorted((r, n) for r, n in inserted_after.items() if n)
        self.after = [r for r, _ in self.points]
        self.cumulative = []
        total = 0
        for _, n in self.points:
            total += n
            self.cumulative.append(total)

    def __call__(self, row: int) -> int:
        i = bisect_left(self.after, row)  # insertion points strictly above `row`
        return row + (self.cumulative[i - 1] if i else 0)

    def __bool__(self) -> bool:
        return bool(self.points)


def shift_ref(ref: str, fn: RowShift) -> str:
    """'A5', '$B$7:C9', '5:7' -> shifted. Whole columns ('A:C') and names are left alone."""
    out = []
    for part in ref.split(":"):
        m = CELL_REF.fullmatch(part)
        out.append(f"{m.group(1) or ''}{m.group(2)}{fn(int(m.group(3)))}" if m else part)
    return ":".join(out)


def shift_sqref(sqref: str, fn: RowShift) -> str:
    return " ".join(shift_ref(p, fn) for p in sqref.split())


def _sheet_name(prefix: str) -> str | None:
    name = prefix.strip()
    if name.startswith("'") and name.endswith("'"):
        name = name[1:-1].replace("''", "'")
    if name.startswith("["):  # another workbook
        return None
    return name


def shift_formula(formula: str, fn: RowShift, target: str, own_sheet: bool) -> str:
    """Shift references to `target` in a formula (no leading '='). Unprefixed references belong
    to the sheet the formula is on, so they move only when that sheet is `target`."""
    if not formula or not any(ch.isdigit() for ch in formula):
        return formula
    try:
        tok = Tokenizer("=" + formula)
    except Exception:  # an expression the tokenizer cannot read is left exactly as it was
        return formula
    changed = False
    for t in tok.items:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        value = t.value
        if "!" in value:
            prefix, ref = value.rsplit("!", 1)
            if _sheet_name(prefix.split(":")[0]) != target:
                continue
            new = f"{prefix}!{shift_ref(ref, fn)}"
        elif own_sheet:
            new = shift_ref(value, fn)
        else:
            continue
        if new != value:
            t.value, changed = new, True
    return tok.render()[1:] if changed else formula


def _shift_formula_xml(text: str, fn: RowShift, target: str, own_sheet: bool) -> str:
    raw = _unesc(text)
    new = shift_formula(raw, fn, target, own_sheet)
    return text if new == raw else escape(new)


def _expand_cell(doc: SheetDoc, col: str, row: int, inner: str, target: str, own_sheet: bool) -> str:
    """Turn a shared-formula cell into a plain formula at its own (unchanged) address.

    Rows are renumbered independently, so a shared formula cannot stay shared on the sheet
    that gets rows; on other sheets only those that point at that sheet are expanded."""
    f = SHARED_F_RE.search(inner)
    if not f:
        return inner
    si = re.search(r'si="(\d+)"', f.group(1)).group(1)
    formula, origin = doc.shared[si]
    if not own_sheet and target not in _unesc(formula):
        return inner
    text = Translator("=" + _unesc(formula), origin=origin).translate_formula(f"{col}{row}")[1:]
    return inner[:f.start()] + f"<f>{escape(text)}</f>" + inner[f.end():]


def _rewrite_row(doc: SheetDoc, row: RawRow, new_number: int, fn: RowShift, target: str, own_sheet: bool) -> RawRow:
    if row.body is None:
        return RawRow(new_number, row.attrs, None)

    def fix_cell(m: re.Match) -> str:
        col, old_r, attrs, inner = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        dest = f"{col}{new_number}"
        if inner is None:
            return f'<c r="{dest}"{attrs}/>'
        inner = _expand_cell(doc, col, old_r, inner, target, own_sheet)

        def fix_f(fm: re.Match) -> str:
            fattrs = fm.group(1) or ""
            if fn and 'ref="' in fattrs:  # array formula range
                fattrs = re.sub(r'ref="([^"]*)"', lambda r: f'ref="{shift_sqref(r.group(1), fn)}"', fattrs)
            return f"<f{fattrs}>{_shift_formula_xml(fm.group(2), fn, target, own_sheet)}</f>"
        inner = ANY_F_RE.sub(fix_f, inner)
        return f'<c r="{dest}"{attrs}>{inner}</c>'

    return RawRow(new_number, row.attrs, CELL_RE.sub(fix_cell, row.body))


def shift_other_sheet(xml: str, fn: RowShift, target: str) -> str:
    """Formulas on another sheet that point at the target sheet."""
    if target not in xml and target.replace("'", "''") not in xml:
        return xml
    doc = SheetDoc.parse(xml)
    doc.rows = [_rewrite_row(doc, r, r.number, fn, target, False) for r in doc.rows]
    doc.tail = _shift_tail_formulas(doc.tail, fn, target, False)
    return doc.serialize()


ATTR_REF = re.compile(r'\b(ref|sqref|activeCell|topLeftCell)="([^"]*)"')


def _shift_attrs(xml: str, fn: RowShift) -> str:
    return ATTR_REF.sub(lambda m: f'{m.group(1)}="{shift_sqref(m.group(2), fn)}"', xml)


def _shift_tail_formulas(xml: str, fn: RowShift, target: str, own_sheet: bool) -> str:
    """Formulas in conditional formats and data validations (both the classic and x14 forms)."""
    def fix(m: re.Match) -> str:
        return f"{m.group(1)}{_shift_formula_xml(m.group(2), fn, target, own_sheet)}{m.group(3)}"
    return re.sub(r"(<(?:formula|formula1|formula2|xm:f)>)(.*?)(</(?:formula|formula1|formula2|xm:f)>)", fix, xml, flags=re.S)


def _shift_sheet_tail(xml: str, fn: RowShift, target: str) -> str:
    xml = _shift_attrs(xml, fn)
    xml = re.sub(r"(<xm:sqref>)(.*?)(</xm:sqref>)", lambda m: f"{m.group(1)}{shift_sqref(m.group(2), fn)}{m.group(3)}", xml)
    xml = re.sub(r'(<brk\b[^>]*\bid=")(\d+)', lambda m: f"{m.group(1)}{fn(int(m.group(2)))}", xml)
    return _shift_tail_formulas(xml, fn, target, True)


def _rels_targets(pkg: XlsxPackage, part: str) -> list[tuple[str, str]]:
    """(type suffix, part name) for every relationship of a part."""
    folder, name = posixpath.split(part)
    rels = f"{folder}/_rels/{name}.rels"
    if rels not in pkg.parts:
        return []
    out = []
    for m in re.finditer(r"<Relationship\b([^>]*)/?>", pkg.text(rels)):
        attrs = m.group(1)
        target = re.search(r'Target="([^"]*)"', attrs)
        kind = re.search(r'Type="([^"]*)"', attrs)
        if not target or 'TargetMode="External"' in attrs:
            continue
        path = target.group(1)
        path = path.lstrip("/") if path.startswith("/") else posixpath.normpath(posixpath.join(folder, path))
        out.append(((kind.group(1) if kind else "").rsplit("/", 1)[-1], path))
    return out


def insert_rows(pkg: XlsxPackage, sheet: str, inserts: dict[int, list[RawRow]]) -> tuple[SheetDoc, dict[int, int], list[int], set[str]]:
    """Insert rendered rows after given source rows of `sheet` and move every reference.

    inserts: source row -> rows to place directly below it (their numbers are assigned here;
    each RawRow body must be a callable taking the final row number). Returns the updated sheet
    document (not yet written back), old -> new row numbers, the new rows' numbers, and the
    names of the other parts that changed."""
    part = pkg.sheet_part(sheet)
    doc = SheetDoc.parse(pkg.text(part))
    fn = RowShift({r: len(v) for r, v in inserts.items()})
    mapping: dict[int, int] = {}
    added: list[int] = []
    rows: list[RawRow] = []
    existing = {r.number for r in doc.rows}
    for r in sorted(set(inserts) - existing):  # an insertion point on a row Excel did not store
        doc.rows.append(RawRow(r, "", None))
    doc.rows.sort(key=lambda r: r.number)
    for row in doc.rows:
        new = fn(row.number)
        mapping[row.number] = new
        rows.append(_rewrite_row(doc, row, new, fn, sheet, True))
        for k, extra in enumerate(inserts.get(row.number, []), start=1):
            n = new + k
            added.append(n)
            rows.append(RawRow(n, extra.attrs, extra.body(n) if callable(extra.body) else extra.body))
    doc.rows = rows
    doc.shared = {}
    touched: set[str] = set()
    if not fn:
        return doc, mapping, added, touched
    doc.head = _shift_attrs(doc.head, fn)
    doc.tail = _shift_sheet_tail(doc.tail, fn, sheet)

    def update(name: str, new: str) -> None:
        if pkg.text(name) != new:
            pkg.set_text(name, new)
            touched.add(name)

    # Defined names: print areas, filter ranges, named ranges on this sheet.
    wb = pkg.text("xl/workbook.xml")
    update("xl/workbook.xml", re.sub(
        r"(<definedName\b[^>]*>)(.*?)(</definedName>)",
        lambda m: f"{m.group(1)}{_shift_formula_xml(m.group(2), fn, sheet, False)}{m.group(3)}", wb, flags=re.S))
    # Other worksheets whose formulas point here.
    for name in list(pkg.parts):
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml") and name != part:
            update(name, shift_other_sheet(pkg.text(name), fn, sheet))
    # Chart series and pivot sources anywhere in the workbook.
    for name in list(pkg.parts):
        if name.startswith("xl/charts/chart") and name.endswith(".xml"):
            update(name, re.sub(r"(<c:f>)(.*?)(</c:f>)", lambda m: f"{m.group(1)}"
                                f"{_shift_formula_xml(m.group(2), fn, sheet, False)}{m.group(3)}", pkg.text(name), flags=re.S))
        elif name.startswith("xl/pivotCache/pivotCacheDefinition") and name.endswith(".xml"):
            update(name, re.sub(r'<worksheetSource\b[^>]*/>', lambda m: _shift_attrs(m.group(0), fn)
                                if re.search(rf'sheet="{re.escape(escape(sheet))}"', m.group(0)) else m.group(0),
                                pkg.text(name)))
    # Parts that belong to this sheet: tables, comments, drawings (anchors are 0-based rows).
    for kind, target in _rels_targets(pkg, part):
        if target not in pkg.parts:
            continue
        if kind in ("table", "comments", "threadedComment"):
            update(target, _shift_attrs(pkg.text(target), fn))
        elif kind == "drawing":
            update(target, re.sub(r"(<xdr:row>)(\d+)(</xdr:row>)",
                                  lambda m: f"{m.group(1)}{fn(int(m.group(2)) + 1) - 1}{m.group(3)}", pkg.text(target)))
        elif kind == "vmlDrawing":
            update(target, re.sub(r"(<x:Row>)(\d+)(</x:Row>)",
                                  lambda m: f"{m.group(1)}{fn(int(m.group(2)) + 1) - 1}{m.group(3)}", pkg.text(target)))
            update(target, re.sub(r"(<x:Anchor>\s*)([\d,\s]+)(</x:Anchor>)", lambda m: m.group(1) + _vml_anchor(
                m.group(2), fn) + m.group(3), pkg.text(target)))
    return doc, mapping, added, touched


def _vml_anchor(text: str, fn: RowShift) -> str:
    """LeftCol, LeftOff, TopRow, TopOff, RightCol, RightOff, BottomRow, BottomOff (0-based rows)."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 8 or not all(p.isdigit() for p in parts):
        return text
    for i in (2, 6):
        parts[i] = str(fn(int(parts[i]) + 1) - 1)
    return ", ".join(parts)
