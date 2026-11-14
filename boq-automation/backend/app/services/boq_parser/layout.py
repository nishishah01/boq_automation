"""Work out where things are in a BOQ worksheet, whatever its layout.

Two formats are recognised:
  sales_bom  the company's import template (Unique No, Sales Item, ClassName ...). Unchanged.
  generic    any other BOQ: a header row somewhere near the top, a description column and,
             usually, serial number, unit and quantity columns in any order and under any name.

A layout is plain JSON stored with the upload, so an engineer can confirm or correct it:
  {"format", "sheet", "header_row", "columns": {role: 0-based column}, "headers": [...],
   "confidence": "high" | "low" | "none", "needs_mapping": bool, "notes": [...]}
"""
import re

from app.services.excel_processor.workbook import BOQ_REQUIRED, find_header

# Column roles a generic BOQ may have. Only "description" is mandatory.
ROLES: dict[str, list[str]] = {
    "description": ["description", "item description", "boq description", "description of item",
                    "description of items", "description of work", "description of works", "work description",
                    "detailed description", "scope of work", "scope of works", "scope", "particulars",
                    "item brief", "item details", "specification", "specifications", "item name", "items",
                    "name of item", "description of materials", "material description", "work item", "details"],
    "serial": ["s no", "sl no", "sr no", "s n", "serial no", "serial number", "sno", "slno", "srno", "unique no",
               "item no", "item number", "no", "ref", "ref no", "reference", "boq ref", "boq no", "boq item no",
               "item code", "code", "clause", "clause no", "sl", "sr", "s"],
    "quantity": ["qty", "quantity", "billable qty", "boq qty", "total qty", "est qty", "estimated qty", "nos",
                 "quantities", "qnty", "qty nos"],
    "unit": ["unit", "units", "uom", "qty uom", "unit of measurement", "unit of measure", "u m", "um"],
    "rate": ["rate", "unit rate", "unit price", "price", "supply rate", "rate per unit", "basic rate"],
    "amount": ["amount", "total amount", "total", "value", "total value", "total price", "amount inr", "amount rs"],
    "cost_sheet_type": ["cost sheet type", "cost sheet", "cost type", "costsheet type"],
    # The Item Master number of a component row, written by this app's export of a generic BOQ.
    "mapped_item_no": ["component item no", "mapped item no", "item master no", "item master code",
                       "component item number", "component code", "item master item no"],
}
ROLE_LABELS = {"description": "Description", "serial": "Serial / Item No.", "quantity": "Quantity", "unit": "Unit",
               "rate": "Rate", "amount": "Amount", "cost_sheet_type": "Cost Sheet Type",
               "mapped_item_no": "Component Item No."}
HEADER_SCAN = 40


def norm_header(value) -> str:
    text = str(value or "").lower().replace("\n", " ")
    text = re.sub(r"\(.*?\)", " ", text)  # "Amount (INR)" -> "amount"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def role_score(header: str, role: str) -> float:
    """1.0 for a known name, 0.7 when a known name starts the header, else 0."""
    h = norm_header(header)
    if not h:
        return 0.0
    names = ROLES[role]
    if h in names:
        return 1.0
    if role == "quantity" and h.endswith(" total") and any(h.startswith(n + " ") for n in names):
        return 0.95  # "Quantity Total" beats "Quantity Level 7"
    if role != "serial" and any(len(n) > 3 and (h.startswith(n + " ") or h.endswith(" " + n)) for n in names):
        return 0.7
    return 0.0


def combined_headers(rows: list[list], idx: int) -> list[str]:
    """Header names of row idx, joined with a sub-header row beneath it when there is one.

    "Quantity" merged over three columns with "Level 7 | Level 13 | Total" below becomes
    "Quantity Level 7", "Quantity Level 13", "Quantity Total"."""
    top = _cells(rows[idx]) if idx < len(rows) else []
    below = _cells(rows[idx + 1]) if idx + 1 < len(rows) else []
    numeric = [v for v in (rows[idx + 1] if idx + 1 < len(rows) else []) if isinstance(v, (int, float))]
    labels = [b for b in below if b]
    if not any(top) or not labels or numeric or any(len(b) > 40 for b in labels):
        return top
    width = max(len(top), len(below))
    top += [""] * (width - len(top))
    below += [""] * (width - len(below))
    # A real sub-header has labels under the blank cells of a heading merged across columns.
    under_span = any(b and not t and any(top[:i]) for i, (t, b) in enumerate(zip(top, below)))
    if not under_span:
        return top[:len(_cells(rows[idx]))]
    out, last = [], ""
    for t, b in zip(top, below):
        if t:
            last = t
        parent = t or (last if b else "")  # a blank top cell under a merged heading belongs to it
        out.append(f"{parent} {b}".strip() if b else t)
    return out


def _cells(row) -> list[str]:
    return [str(c).strip() if c is not None else "" for c in row]


def assign_roles(headers: list[str]) -> dict[str, tuple[int, float]]:
    """Best column per role, each column used once, strongest matches first."""
    pairs = sorted(((role_score(h, r), r, i) for r in ROLES for i, h in enumerate(headers)), reverse=True)
    taken_cols, out = set(), {}
    for score, role, i in pairs:
        if score <= 0 or role in out or i in taken_cols:
            continue
        out[role] = (i, score)
        taken_cols.add(i)
    # Prefer a real "Description" over "Item Brief" when both exist.
    if "description" in out and norm_header(headers[out["description"][0]]) == "item brief":
        for i, h in enumerate(headers):
            if norm_header(h) in ("description", "item description", "boq description") and i not in taken_cols:
                out["description"] = (i, 1.0)
    return out


def _texty_column(rows: list[list], start: int) -> int | None:
    """The column holding the longest text: the likely description when no header names it."""
    lengths: dict[int, list[int]] = {}
    for row in rows[start:start + 80]:
        for i, v in enumerate(row):
            if isinstance(v, str) and not v.strip().replace(".", "").isdigit():
                lengths.setdefault(i, []).append(len(v.strip()))
    scored = {i: sum(l) / len(rows[start:start + 80] or [1]) for i, l in lengths.items() if len(l) >= 3}
    return max(scored, key=scored.get) if scored else None


def detect_layout(ws) -> dict:
    """Find the header row and column roles of one worksheet."""
    sheet = ws.title
    sales = find_header(ws, BOQ_REQUIRED, scan=10)
    if sales:
        header_row, header = sales
        return {"format": "sales_bom", "sheet": sheet, "header_row": header_row, "headers": header,
                "columns": {}, "confidence": "high", "needs_mapping": False, "notes": []}

    rows = [list(r) for r in ws.iter_rows(min_row=1, max_row=HEADER_SCAN + 80, values_only=True)]
    best: tuple[float, int, dict] | None = None
    for idx, row in enumerate(rows[:HEADER_SCAN]):
        roles = assign_roles(combined_headers(rows, idx))
        if not roles:
            continue
        score = sum(s for _, s in roles.values()) + (1.5 if "description" in roles else 0)
        if best is None or score > best[0]:
            best = (score, idx, roles)

    notes: list[str] = []
    if best and "description" in best[2]:
        _, idx, roles = best
        header_row = idx + 1
        confidence = "high" if roles["description"][1] >= 1 else "low"
        missing = [ROLE_LABELS[r] for r in ("unit", "quantity") if r not in roles]
        if missing:  # every line would be skipped without them, so ask rather than guess
            confidence = "low"
            notes.append(f"No {' or '.join(missing).lower()} column was recognised. Unit and quantity are "
                         "required: only BOQ items with both are mapped.")
        if confidence == "low":
            notes.append(f"The description column was guessed from its header \"{_cells(rows[idx])[roles['description'][0]]}\".")
    else:
        idx = best[1] if best else None
        roles = best[2] if best else {}
        start = idx + 1 if idx is not None else 0
        guess = _texty_column(rows, start)
        header_row = idx + 1 if idx is not None else None
        if guess is not None and guess not in {c for c, _ in roles.values()}:
            roles["description"] = (guess, 0.0)
            notes.append("No column is named like a description; the column with the longest text was suggested.")
        confidence = "low" if "description" in roles else "none"
        if confidence == "none":
            notes.append("No description column was found. A description column is mandatory.")

    headers = combined_headers(rows, header_row - 1) if header_row and header_row <= len(rows) else []
    return {"format": "generic", "sheet": sheet, "header_row": header_row, "headers": headers,
            "columns": {r: c for r, (c, _) in roles.items()}, "confidence": confidence,
            "needs_mapping": confidence != "high", "notes": notes}


def validate_layout(layout: dict, require_unit_qty: bool = True) -> list[str]:
    """Problems that stop a generic layout being used for matching."""
    if layout.get("format") == "sales_bom":
        return []
    errors = []
    cols = layout.get("columns") or {}
    if cols.get("description") is None:
        errors.append("A description column is mandatory: it is what the AI reads to find components. "
                      "Choose the column that describes each BOQ item.")
    for role in ("unit", "quantity") if require_unit_qty else ():
        if cols.get(role) is None:
            errors.append(f"Choose the {ROLE_LABELS[role].lower()} column: only BOQ items with both a unit "
                          "and a quantity are mapped to components.")
    if not layout.get("header_row"):
        errors.append("Choose the row that holds the column headings.")
    used = [c for c in cols.values() if c is not None]
    if len(used) != len(set(used)):
        errors.append("Each column can be used for one field only.")
    return errors


def layout_at(ws, header_row: int) -> dict:
    """Column suggestions for a header row the engineer picked."""
    rows = [list(r) for r in ws.iter_rows(min_row=header_row, max_row=header_row + 1, values_only=True)]
    headers = combined_headers(rows, 0) if rows else []
    roles = assign_roles(headers)
    confidence = "high" if roles.get("description", (0, 0))[1] >= 1 else ("low" if "description" in roles else "none")
    return {"format": "generic", "sheet": ws.title, "header_row": header_row, "headers": headers,
            "columns": {r: c for r, (c, _) in roles.items()}, "confidence": confidence,
            "needs_mapping": True, "notes": []}
