"""Direct OOXML package editing.

openpyxl cannot round-trip the BOQ template: on save it drops the macro-button shapes, the
x14 data validations and the dynamic-array cell metadata that the Summary sheet relies on.
So anything that writes a workbook works on the package itself, and every part that is not
deliberately changed is copied byte for byte.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree
from openpyxl.formula.translate import Translator
from openpyxl.utils import column_index_from_string

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

ROW_RE = re.compile(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', re.S)
CELL_RE = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
SHARED_F_RE = re.compile(r'<f t="shared"([^>]*?)(?:/>|>(.*?)</f>)', re.S)
PLAIN_F_RE = re.compile(r"<f>(.*?)</f>", re.S)


class OoxmlError(Exception):
    pass


class XlsxPackage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as z:
            self.infos = z.infolist()
            self.parts: dict[str, bytes] = {i.filename: z.read(i.filename) for i in self.infos}

    def text(self, name: str) -> str:
        return self.parts[name].decode("utf-8")

    def set_text(self, name: str, value: str) -> None:
        self.parts[name] = value.encode("utf-8")

    def sheet_part(self, sheet_name: str) -> str:
        wb = etree.fromstring(self.parts["xl/workbook.xml"])
        rels = etree.fromstring(self.parts["xl/_rels/workbook.xml.rels"])
        targets = {r.get("Id"): r.get("Target") for r in rels}
        for s in wb.find(f"{{{NS}}}sheets"):
            if s.get("name") == sheet_name:
                target = targets[s.get(f"{{{RELS_NS}}}id")]
                return target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        raise OoxmlError(f"Worksheet not found: {sheet_name}")

    def drop_calc_chain(self) -> None:
        """Excel rebuilds calcChain silently; a stale one triggers the repair dialog."""
        if "xl/calcChain.xml" not in self.parts:
            return
        del self.parts["xl/calcChain.xml"]
        ct = self.text("[Content_Types].xml")
        self.set_text("[Content_Types].xml", re.sub(r'<Override PartName="/xl/calcChain\.xml"[^>]*/>', "", ct))
        rels = self.text("xl/_rels/workbook.xml.rels")
        self.set_text("xl/_rels/workbook.xml.rels", re.sub(r'<Relationship [^>]*calcChain[^>]*/>', "", rels))

    def force_full_recalc(self) -> None:
        wb = self.text("xl/workbook.xml")
        if "fullCalcOnLoad" in wb:
            return
        if "<calcPr" in wb:
            wb = re.sub(r"<calcPr\b", '<calcPr fullCalcOnLoad="1"', wb, count=1)
        else:
            wb = wb.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
        self.set_text("xl/workbook.xml", wb)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        if path.resolve() == self.path.resolve():
            raise OoxmlError("Refusing to overwrite the source workbook")
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for info in self.infos:  # keeps original part order; [Content_Types] stays first
                if info.filename in self.parts:
                    z.writestr(info.filename, self.parts[info.filename])


class SharedStrings:
    """Append-only view of sharedStrings.xml; existing indexes never move."""

    def __init__(self, pkg: XlsxPackage):
        self.pkg = pkg
        self.xml = pkg.text("xl/sharedStrings.xml")
        root = etree.fromstring(pkg.parts["xl/sharedStrings.xml"])
        self.count = 0
        self.index: dict[str, int] = {}
        for i, si in enumerate(root):
            self.count += 1
            kids = list(si)
            if len(kids) == 1 and kids[0].tag == f"{{{NS}}}t":  # rich text keeps its own runs
                self.index.setdefault(kids[0].text or "", i)
        self.added: list[str] = []
        self.refs_added = 0

    def get(self, text: str) -> int:
        self.refs_added += 1
        if text in self.index:
            return self.index[text]
        i = self.count
        self.count += 1
        self.index[text] = i
        self.added.append(text)
        return i

    def flush(self) -> None:
        extra = "".join(f'<si><t xml:space="preserve">{escape(t)}</t></si>' for t in self.added)
        xml = self.xml.replace("</sst>", extra + "</sst>")

        def bump(m: re.Match) -> str:
            attr, value = m.group(1), int(m.group(2))
            delta = len(self.added) if attr == "uniqueCount" else self.refs_added
            return f'{attr}="{value + delta}"'

        head_end = xml.index(">", xml.index("<sst"))
        head = re.sub(r'\b(uniqueCount|count)="(\d+)"', bump, xml[:head_end])
        self.pkg.set_text("xl/sharedStrings.xml", head + xml[head_end:])


@dataclass
class RawRow:
    number: int
    attrs: str
    body: str | None  # None for a self-closed row


@dataclass
class Formula:
    text: str
    cached: float | None = None


@dataclass
class SheetDoc:
    """A worksheet split around its <sheetData>, with rows kept as raw XML."""
    head: str
    rows: list[RawRow]
    tail: str
    shared: dict[str, tuple[str, str]] = field(default_factory=dict)  # si -> (formula, origin)

    @classmethod
    def parse(cls, xml: str) -> "SheetDoc":
        start = xml.index("<sheetData")
        open_end = xml.index(">", start) + 1
        end = xml.index("</sheetData>")
        rows = [RawRow(int(m[1]), m[2], m[3]) for m in ROW_RE.finditer(xml[open_end:end])]
        doc = cls(xml[:open_end], rows, xml[end:])
        for row in rows:
            for c in CELL_RE.finditer(row.body or ""):
                f = SHARED_F_RE.search(c.group(4) or "")
                if f and f.group(2):
                    si = re.search(r'si="(\d+)"', f.group(1)).group(1)
                    doc.shared[si] = (f.group(2), f"{c.group(1)}{c.group(2)}")
        return doc

    def row_map(self) -> dict[int, RawRow]:
        return {r.number: r for r in self.rows}

    def serialize(self) -> str:
        out = [self.head]
        for r in self.rows:
            out.append(f'<row r="{r.number}"{r.attrs}/>' if r.body is None
                       else f'<row r="{r.number}"{r.attrs}>{r.body}</row>')
        out.append(self.tail)
        return "".join(out)


def _unesc(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def assert_same_row_formulas(doc: SheetDoc) -> None:
    """Row shifting below is only valid for formulas that reference their own row."""
    ref = re.compile(r"\$?[A-Z]{1,3}\$?(\d+)")
    for r in doc.rows:
        for c in CELL_RE.finditer(r.body or ""):
            body = c.group(4) or ""
            found = [m.group(1) for m in PLAIN_F_RE.finditer(body)]
            f = SHARED_F_RE.search(body)
            if f and f.group(2):
                found.append(f.group(2))
            for formula in found:
                if any(int(n) != int(c.group(2)) for n in ref.findall(formula)):
                    raise OoxmlError(f"Formula at {c.group(1)}{c.group(2)} references another row; "
                                     "safe row insertion is not supported for it")


def relocate_row(doc: SheetDoc, row: RawRow, new_number: int) -> RawRow:
    """Renumber a row, expanding shared formulas into plain ones at their new position."""
    if row.body is None:
        return RawRow(new_number, row.attrs, None)

    def fix_cell(m: re.Match) -> str:
        col, old_r, attrs, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        dest = f"{col}{new_number}"
        if inner is None:
            return f'<c r="{dest}"{attrs}/>'
        f = SHARED_F_RE.search(inner)
        if f:
            si = re.search(r'si="(\d+)"', f.group(1)).group(1)
            formula, origin = doc.shared[si]
            text = Translator("=" + _unesc(formula), origin=origin).translate_formula(dest)[1:]
            inner = inner[:f.start()] + f"<f>{escape(text)}</f>" + inner[f.end():]
        else:
            p = PLAIN_F_RE.search(inner)
            if p and int(old_r) != new_number:
                text = Translator("=" + _unesc(p.group(1)), origin=f"{col}{old_r}").translate_formula(dest)[1:]
                inner = inner[:p.start()] + f"<f>{escape(text)}</f>" + inner[p.end():]
        return f'<c r="{dest}"{attrs}>{inner}</c>'

    return RawRow(new_number, row.attrs, CELL_RE.sub(fix_cell, row.body))


def cell_styles(row: RawRow) -> dict[str, str]:
    """Column letter -> style index for a row."""
    styles = {}
    for m in CELL_RE.finditer(row.body or ""):
        s = re.search(r'\bs="(\d+)"', m.group(3))
        if s:
            styles[m.group(1)] = s.group(1)
    return styles


def render_cell(col: str, row: int, value, styles: dict[str, str], sst: SharedStrings) -> str:
    s = f' s="{styles[col]}"' if col in styles else ""
    ref = f"{col}{row}"
    if isinstance(value, Formula):
        v = "" if value.cached is None else f"<v>{value.cached}</v>"
        return f'<c r="{ref}"{s}><f>{escape(value.text)}</f>{v}</c>'
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}"{s} t="s"><v>{sst.get(str(value))}</v></c>'


def render_row_body(values: dict[str, object], row: int, styles: dict[str, str], sst: SharedStrings) -> str:
    cols = sorted(set(values) | set(styles), key=column_index_from_string)
    return "".join(render_cell(c, row, values.get(c), styles, sst) for c in cols)
