"""Read the verified parent -> component mappings out of a completed BOQ.

A historical BOQ is reference data: nothing in it is mapped or changed, and the file is only
ever opened read-only. Every worksheet is examined, in whatever layout it uses:

  Sales BOM template   components are the child rows (ClassName / Item No.) under each sales
                       line, with the Cost Sheet Type the engineer gave them. Two variants
                       occur and both are read:
                         imported   Unique No / ParentUniqueNo hold the hierarchy
                         working    Unique No is still blank (or Sales Item is blank): the
                                    hierarchy is read from row order and Cust Ref No.
  any other layout     components are rows (or cells) holding an Item Master number. The
                       column is found by its header or, failing that, by its values: the one
                       whose cells are mostly real Item Master numbers.

Rejected, with a reason, rather than guessed at:
  * a flat list of components with no parent descriptions above them (an ERP export);
  * worksheets with no Item Master numbers at all (client / OC BOQs, Job Code forms).

Only BOQ items that actually have components become knowledge. Each one keeps its source
(worksheet, row of the parent and of every component) so a suggestion can be traced back.
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.boq_parser.layout import detect_layout
from app.services.boq_parser.parser import CHILD_CLASSES, ParsedBoq, _serial_level, parse_generic
from app.services.excel_processor.workbook import BOQ_REQUIRED, WorkbookError, find_header, open_values_tolerant

MIN_ITEM_HITS = 2  # cells that must hold real Item Master numbers before a column is trusted
# Without an Item Master to compare with, codes such as CO-21405 or IN-0042 are recognised by shape.
ITEM_CODE_RE = re.compile(r"^[A-Z]{2,4}-\d{3,}[A-Z0-9-]*$")
FLAT_LIST_SHARE = 0.9  # rows carrying an Item Number, of all described rows: a component list, not a BOQ


@dataclass
class ExtractedComponent:
    item_no: str
    item_brief: str
    cost_sheet_type: str  # as written in the historical BOQ ("" when the engineer left it blank)
    class_name: str
    row: int = 0
    uom: str = ""
    quantity: float = 0.0


@dataclass
class ExtractedParent:
    sheet: str
    row: int
    unique_no: str
    description: str
    context: list[str]
    uom: str
    quantity: float
    cost_sheet_type: str
    components: list[ExtractedComponent] = field(default_factory=list)
    method: str = ""  # how the parent-child link was read: hierarchy | row order | generic


@dataclass
class Extraction:
    parents: list[ExtractedParent]
    sheets: list[dict]  # per worksheet: name, format, parents, components, note
    warnings: list[str]
    repaired: str = ""  # set when values had to be read from a repaired in-memory copy
    orphans: int = 0  # component rows that could not be tied to a parent description
    orphan_rows: list[dict] = field(default_factory=list)  # {sheet, row, item_no, cost_sheet_type, brief}

    @property
    def fingerprint(self) -> str:
        """Same mappings, same fingerprint: catches a re-saved copy of a BOQ already uploaded."""
        keys = sorted(f"{p.description.strip().upper()}|{'/'.join(sorted(c.item_no for c in p.components))}"
                      for p in self.parents)
        return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


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


def item_number_column(ws, header_row: int, known: set[str], exclude: set[int]) -> int | None:
    """The column whose cells are most often real Item Master numbers (or, with no Item Master
    loaded yet, look like item codes)."""
    def is_item(text: str) -> bool:
        return text in known if known else bool(ITEM_CODE_RE.match(text))
    hits: dict[int, int] = {}
    filled: dict[int, int] = {}
    for values in ws.iter_rows(min_row=header_row + 1, max_row=header_row + 3000, values_only=True):
        for i, v in enumerate(values):
            if i not in exclude and v not in (None, ""):
                filled[i] = filled.get(i, 0) + 1
                if any(is_item(_norm(part)) for part in str(v).splitlines()):
                    hits[i] = hits.get(i, 0) + 1
    if not hits:
        return None
    col = max(hits, key=hits.get)
    # Enough real Item Master numbers, or a column that holds little else.
    return col if hits[col] >= MIN_ITEM_HITS or hits[col] * 2 >= filled[col] else None


# ---------------- Sales BOM template (imported and working variants) ----------------

@dataclass
class _Row:
    r: int
    uno: str
    pno: str
    cust: str
    brief: str
    desc: str
    sales: str
    ctype: str
    qty: float
    uom: str
    cls: str
    item: str

    @property
    def text(self) -> str:
        return (self.desc or self.brief).strip()

    @property
    def is_component(self) -> bool:
        return self.cls in CHILD_CLASSES or bool(self.item)

    @property
    def is_section(self) -> bool:
        # Drafts sometimes carry the "Section" marker in the unit column instead.
        return self.ctype.lower() == "section" or self.uom.lower() == "section"

    @property
    def is_line(self) -> bool:
        """A billable BOQ item. Working copies sometimes leave Sales Item blank and mark the
        line only by its Cost Sheet Type, unit or quantity (headings have none of these)."""
        if self.is_component or not self.text:
            return False
        if self.sales == "Y":  # the template's own marker wins, even on a row typed "Section"
            return True
        return self.sales == "" and not self.is_section and bool(self.ctype or self.uom or self.qty)


def _sales_bom(ws, header_row: int, header: list[str]) -> tuple[list[ExtractedParent], list[str], list[dict]]:
    col = {name: i for i, name in enumerate(header) if name}

    def at(v, name):
        i = col.get(name)
        return v[i] if i is not None and i < len(v) else None

    rows: list[_Row] = []
    for r, v in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        x = _Row(r, _s(at(v, "Unique No")), _s(at(v, "ParentUniqueNo")), _s(at(v, "Cust Ref No.")),
                 _s(at(v, "Item Brief")), _s(at(v, "Description")), _s(at(v, "Sales Item")).upper(),
                 _s(at(v, "Cost Sheet Type")), _f(at(v, "Billable Qty")), _s(at(v, "Qty UOM")),
                 _s(at(v, "ClassName")), _s(at(v, "Item No.")))
        if x.text or x.item or x.uno:
            rows.append(x)

    by_no = {x.uno: x for x in rows if x.uno}

    def parent_of(x: _Row) -> str:
        return x.pno or (x.uno.rsplit(".", 1)[0] if "." in x.uno else "")

    def chain(x: _Row) -> list[_Row]:
        out, seen, cur = [], {x.uno}, parent_of(x)
        while cur and cur in by_no and cur not in seen:
            seen.add(cur)
            out.append(by_no[cur])
            cur = parent_of(by_no[cur])
        return out

    parents: dict[int, ExtractedParent] = {}
    order: list[ExtractedParent] = []
    warnings: list[str] = []
    orphans: list[dict] = []
    latest_by_ref: dict[str, _Row] = {}  # row order: most recent heading/line per Cust Ref No.
    current: _Row | None = None  # row order: the line components below belong to

    def context_by_order(x: _Row) -> list[str]:
        out = []
        ref = x.cust.rstrip(".")
        while "." in ref:
            ref = ref.rsplit(".", 1)[0]
            a = latest_by_ref.get(ref)
            if a is not None and a.text:
                out.append(a.text)
        if not out:  # no numbering: the nearest section above
            sec = next((a for a in reversed(rows[:rows.index(x)]) if a.is_section and a.cust), None)
            if sec is not None:
                out.append(sec.text)
        a = latest_by_ref.get(ref)
        if a is not None and a is not x and a.text and a.text not in out:
            out.append(a.text)
        return out

    def parent_for(line: _Row, method: str) -> ExtractedParent:
        p = parents.get(line.r)
        if p is None:
            if method == "hierarchy":
                ctx = [a.text for a in chain(line) if a.sales != "Y" and "." in a.uno and a.text]
            else:
                ctx = context_by_order(line)
            p = ExtractedParent(ws.title, line.r, line.uno or line.cust, line.text, ctx, line.uom, line.qty,
                                line.ctype if not line.is_section else "", method=method)
            parents[line.r] = p
            order.append(p)
        return p

    for x in rows:
        if x.is_component:
            owner, method = None, ""
            if x.uno:
                anc = chain(x)
                owner = next((a for a in anc if a.is_line), None)
                if owner is None and anc and not anc[0].is_component and not anc[0].is_section and anc[0].text:
                    owner = anc[0]  # an unmarked draft line: the component's own parent row
                method = "hierarchy"
            if owner is None and current is not None:
                owner, method = current, "row order"
            if owner is None:
                orphans.append({"sheet": ws.title, "row": x.r, "item_no": x.item, "cost_sheet_type": x.ctype,
                                "brief": x.text[:120]})
                if len(warnings) < 20:
                    warnings.append(f"Row {x.r}: component {x.item or x.text} has no parent BOQ description")
                continue
            p = parent_for(owner, method)
            no = x.item.strip()
            if no and no.upper() not in {c.item_no.upper() for c in p.components}:
                p.components.append(ExtractedComponent(no, x.brief or x.desc, x.ctype, x.cls, x.r, x.uom, x.qty))
            continue
        if x.cust:
            latest_by_ref[x.cust.rstrip(".")] = x
        if x.is_line:
            current = x
        elif x.is_section:
            current = None  # a new section: lines above it no longer own what follows
    return [p for p in order if p.components], warnings, orphans


# ---------------- any other layout ----------------

def _from_parsed(parsed: ParsedBoq) -> list[ExtractedParent]:
    out = []
    for line in parsed.lines:
        comps, seen = [], set()
        for ch in line.children:
            no = ch.item_no.strip()
            if not no or no.upper() in seen:
                continue
            seen.add(no.upper())
            comps.append(ExtractedComponent(no, ch.item_brief or ch.description, ch.cost_sheet_type, ch.class_name,
                                            ch.row, ch.uom, ch.billable_qty))
        if comps:
            r = line.row
            out.append(ExtractedParent(parsed.sheet, r.row, r.unique_no, r.text, line.context, r.uom,
                                       r.billable_qty, r.cost_sheet_type, comps, method="generic"))
    return out


def _flat_list(ws, header_row: int, desc_col: int, item_col: int) -> bool:
    """Every described row carries its own Item Number: an exported component list with no
    parent BOQ descriptions, which must not be read as one parent owning everything."""
    described = with_item = 0
    for v in ws.iter_rows(min_row=header_row + 1, max_row=header_row + 3000, values_only=True):
        d = v[desc_col] if desc_col < len(v) else None
        if d in (None, ""):
            continue
        described += 1
        if item_col < len(v) and v[item_col] not in (None, ""):
            with_item += 1
    return described >= 5 and with_item >= FLAT_LIST_SHARE * described


def _generic(ws, layout: dict, known: set[str]) -> tuple[list[ExtractedParent], str, list[str]]:
    if not layout.get("header_row") or layout["columns"].get("description") is None:
        return [], "No description column was found", []
    cols = layout["columns"]
    if cols.get("mapped_item_no") is None:
        # A numbered column may really hold Item Master numbers ("Item Code").
        col = item_number_column(ws, int(layout["header_row"]), known,
                                 {cols["description"]} | ({cols["quantity"]} if "quantity" in cols else set()))
        if col is None:
            return [], "No Item Master numbers in this worksheet (not a mapped BOQ)", []
        cols = {k: v for k, v in cols.items() if v != col}
        cols["mapped_item_no"] = col
        layout = {**layout, "columns": cols, "components_below": True}
    if _flat_list(ws, int(layout["header_row"]), cols["description"], cols["mapped_item_no"]):
        return [], ("A flat list of components with no parent BOQ descriptions; parent-component "
                    "relationships cannot be read from it"), []
    try:
        parsed = parse_generic(ws, layout, require_unit_qty=False)
    except WorkbookError as e:
        return [], str(e), []
    found = _from_parsed(parsed)
    return found, "" if found else "No BOQ item in this worksheet has components", parsed.warnings[:5]


def extract_workbook(path: str | Path, known_item_nos: set[str] | None = None) -> Extraction:
    """Parent descriptions with their verified components, from every worksheet that has them."""
    known = {k.upper() for k in (known_item_nos or set())}
    wb, repaired = open_values_tolerant(path)
    parents: list[ExtractedParent] = []
    sheets: list[dict] = []
    warnings: list[str] = []
    orphans: list[dict] = []
    try:
        if not wb.worksheets:
            raise WorkbookError("The workbook has no worksheets.")
        for ws in wb.worksheets:
            note, found, fmt = "", [], "generic"
            try:
                sales = find_header(ws, BOQ_REQUIRED, scan=10)
                if sales:
                    fmt = "sales_bom"
                    found, w, lost = _sales_bom(ws, *sales)
                    orphans.extend(lost)
                    warnings.extend(f"{ws.title}: {x}" for x in w[:5])
                    if not found:
                        note = "No BOQ item in this worksheet has components (Item Numbers)"
                else:
                    layout = detect_layout(ws)
                    found, note, w = _generic(ws, layout, known)
                    warnings.extend(f"{ws.title}: {x}" for x in w)
            except Exception as e:  # one unreadable worksheet must not lose the others
                note = f"Worksheet could not be read: {e}"
                warnings.append(f"{ws.title}: {note}")
            methods = sorted({p.method for p in found})
            sheets.append({"name": ws.title, "format": fmt, "parents": len(found),
                           "components": sum(len(p.components) for p in found), "note": note,
                           "method": ", ".join(methods)})
            parents.extend(found)
    finally:
        wb.close()
    return Extraction(parents, sheets, warnings, repaired, len(orphans), orphans)
