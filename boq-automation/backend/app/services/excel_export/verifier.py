"""Verify an exported workbook before it can be downloaded. Any failure blocks the download."""
import hashlib
import warnings
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string

warnings.filterwarnings("ignore", module="openpyxl")

# Only these parts may differ from the source workbook.
EXPECTED_CHANGES = {"xl/sharedStrings.xml", "xl/styles.xml", "xl/workbook.xml", "[Content_Types].xml", "xl/_rels/workbook.xml.rels"}
EXPECTED_REMOVED = {"xl/calcChain.xml"}
MUST_SURVIVE_IN_SHEET = ["<sheetProtection", "<conditionalFormatting", "<drawing ", "x14:dataValidation",
                         "<autoFilter", "<pane "]


def _rows(path, sheet: str) -> dict[int, tuple]:
    """Cell values by row number. The workbook is closed at once: on Windows an open read-only
    workbook keeps the file locked, so a failed export could not be deleted."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {i: tuple(r) for i, r in enumerate(wb[sheet].iter_rows(values_only=True), start=1)}
    finally:
        wb.close()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_export(source: str | Path, output: str | Path, sheet: str, info: dict, item_master: dict,
                  allowed_types: list[str] | None = None) -> dict:
    """item_master maps Item No -> Component; allowed_types are the run's Cost Sheet Types.
    Returns {ok, checks, errors}."""
    errors: list[str] = []
    checks: dict[str, object] = {}
    if allowed_types is not None:
        for e in info["expected"]:
            if e["cost_sheet_type"] not in allowed_types:
                errors.append(f"{e['item_no']}: Cost Sheet Type '{e['cost_sheet_type']}' is outside this run's scope")

    with zipfile.ZipFile(source) as a, zipfile.ZipFile(output) as b:
        names_a, names_b = set(a.namelist()), set(b.namelist())
        if names_a - names_b - EXPECTED_REMOVED:
            errors.append(f"Parts lost: {sorted(names_a - names_b - EXPECTED_REMOVED)}")
        if names_b - names_a:
            errors.append(f"Unexpected parts: {sorted(names_b - names_a)}")
        allowed = EXPECTED_CHANGES | {info["sheet_part"]} | set(info.get("touched_parts", []))
        changed = sorted(n for n in names_a & names_b if a.read(n) != b.read(n))
        if set(changed) - allowed:
            errors.append(f"Parts changed unexpectedly: {sorted(set(changed) - allowed)}")
        checks["parts_changed"] = changed
        checks["parts_unchanged"] = len(names_a & names_b) - len(changed)

        if "xl/vbaProject.bin" in names_a:
            same = _sha(a.read("xl/vbaProject.bin")) == _sha(b.read("xl/vbaProject.bin"))
            checks["vba_identical"] = same
            if not same:
                errors.append("VBA project changed")
            checks["vba_sha256"] = _sha(b.read("xl/vbaProject.bin"))

        sheet_xml = b.read(info["sheet_part"]).decode("utf-8")
        original_xml = a.read(info["sheet_part"]).decode("utf-8")
        for marker in MUST_SURVIVE_IN_SHEET:
            if marker in original_xml and marker not in sheet_xml:
                errors.append(f"Sheet feature lost: {marker}")
        checks["sheet_features_kept"] = [m for m in MUST_SURVIVE_IN_SHEET if m in sheet_xml]
        checks["data_validations"] = sheet_xml.count("<x14:dataValidation ")

    if info.get("format") == "generic":
        _verify_generic(source, output, sheet, info, item_master, errors, checks)
        _verify_placement(source, output, sheet, info, errors, checks)
        return {"ok": not errors, "checks": checks, "errors": errors}

    src_rows, out_rows = _rows(source, sheet), _rows(output, sheet)

    # Every original row must exist unchanged at its new position, parent values included.
    moved = 0
    for old, new in info["row_mapping"].items():
        a_row = src_rows.get(old, ())
        b_row = out_rows.get(new, ())
        size = max(len(a_row), len(b_row))
        a_row = tuple(a_row) + (None,) * (size - len(a_row))
        b_row = tuple(b_row) + (None,) * (size - len(b_row))
        if a_row != b_row:
            errors.append(f"Original row {old} changed at output row {new}")
            if len(errors) > 20:
                break
        moved += 1
    checks["original_rows_verified"] = moved

    header = {str(v).strip(): i for i, v in enumerate(out_rows[2]) if v}
    col = lambda r, name: r[header[name]] if len(r) > header[name] else None  # noqa: E731
    by_no = {}
    seen_unique = {}
    for i, r in out_rows.items():
        if i <= 2:
            continue
        no = col(r, "Unique No")
        if no in (None, ""):
            continue
        if no in seen_unique:
            errors.append(f"Duplicate Unique No {no} at rows {seen_unique[no]} and {i}")
        seen_unique[no] = i
        by_no[str(no)] = r

    pairs = set()
    for e in info["expected"]:
        r = by_no.get(e["unique_no"])
        if r is None:
            errors.append(f"Imported row {e['unique_no']} is missing")
            continue
        master = item_master.get(e["item_no"])
        if master is None:
            errors.append(f"{e['item_no']} is not in the Item Master")
            continue
        for name, want in (("Cost Sheet Type", e["cost_sheet_type"]), ("Item No.", master.item_no),
                           ("Item Brief", master.item_brief), ("ClassName", e["class_name"]),
                           ("ParentUniqueNo", e["parent_no"]), ("Sales Item", "N"), ("Qty UOM", e["uom"])):
            got = col(r, name)
            if got != want:
                errors.append(f"{e['unique_no']}: {name} is {got!r}, expected {want!r}")
        if (e["parent_no"], e["item_no"]) in pairs:
            errors.append(f"Duplicate {e['item_no']} under {e['parent_no']}")
        pairs.add((e["parent_no"], e["item_no"]))
    checks["imported_rows_verified"] = len(info["expected"])
    _verify_placement(source, output, sheet, info, errors, checks)
    return {"ok": not errors, "checks": checks, "errors": errors}


def _verify_generic(source, output, sheet: str, info: dict, item_master: dict, errors: list, checks: dict) -> None:
    """Every original row is unchanged at its new position, and every inserted row states
    exactly the Item Master record and the reviewed Cost Sheet Type."""
    a_rows, b_rows = _rows(source, sheet), _rows(output, sheet)
    added_cols = {column_index_from_string(c) - 1 for c in info.get("added_columns", {})}
    mapping = {int(k): v for k, v in info["row_mapping"].items()}
    checked = 0
    for old, a in a_rows.items():
        new = mapping.get(old, old)
        b = b_rows.get(new, ())
        size = max(len(a), len(b))
        a = tuple(a) + (None,) * (size - len(a))
        b = tuple(b) + (None,) * (size - len(b))
        if old == info["header_row"]:
            a = tuple(v for i, v in enumerate(a) if i not in added_cols)
            b = tuple(v for i, v in enumerate(b) if i not in added_cols)
        if a != b:
            errors.append(f"Original row {old} changed at output row {new}")
            if len(errors) > 20:
                return
        checked += 1
    checks["original_rows_verified"] = checked

    col = {k: column_index_from_string(v) - 1 for k, v in info["columns"].items()}
    seen = set()
    for e in info["expected"]:
        row = b_rows.get(e["row"], ())
        got = lambda k: row[col[k]] if len(row) > col[k] else None  # noqa: E731
        master = item_master.get(e["item_no"])
        if master is None:
            errors.append(f"{e['item_no']} is not in the Item Master")
            continue
        for key, label, want in (("mapped_item_no", "Item No.", master.item_no),
                                 ("description", "Component description", master.item_brief),
                                 ("cost_sheet_type", "Cost Sheet Type", e["cost_sheet_type"])):
            if got(key) != want:
                errors.append(f"Row {e['row']}: {label} is {got(key)!r}, expected {want!r}")
        if (e["parent_row"], e["item_no"]) in seen:
            errors.append(f"Row {e['row']}: {e['item_no']} is listed twice under the same BOQ item")
        seen.add((e["parent_row"], e["item_no"]))
    expected_rows = len(a_rows) + len(info.get("added_rows", []))
    if len(b_rows) < expected_rows:
        errors.append(f"The sheet has {len(b_rows)} rows; expected at least {expected_rows}")
    checks["imported_rows_verified"] = len(info["expected"])
    checks["added_columns"] = list(info.get("added_columns", {}).values())


YELLOW = "FFFFFF00"


def _new_row(mapping: dict, row: int) -> int:
    """Where an original row ended up (rows below an insertion move down)."""
    mapping = {int(k): v for k, v in mapping.items()}
    if row in mapping:
        return mapping[row]
    below = [k for k in mapping if k < row]
    return row + (mapping[max(below)] - max(below) if below else 0)


def _fill(cell) -> tuple:
    f = getattr(cell, "fill", None)
    if f is None:
        return ("none", None)
    color = getattr(getattr(f, "fgColor", None), "rgb", None)
    return (f.fill_type or "none", color if isinstance(color, str) else None)


def _verify_placement(source, output, sheet: str, info: dict, errors: list, checks: dict) -> None:
    """Mandatory export conditions, checked on the written file:
      * each parent's new components sit immediately below that parent's block (its own row
        and any rows that already belonged to it), consecutively, with no other BOQ item
        in between, and in the original order of the parents;
      * every inserted row is filled yellow across the whole row;
      * the parent rows and their pre-existing rows keep their own fill (not yellow-washed)."""
    blocks = info.get("blocks") or []
    if not blocks:
        checks["placement_verified"] = 0
        return
    mapping = info["row_mapping"]
    lines = info.get("line_rows") or []
    last_col = int(info.get("fill_columns") or 1)
    wb_out = openpyxl.load_workbook(output, read_only=True)
    wb_src = openpyxl.load_workbook(source, read_only=True)
    try:
        out, src = wb_out[sheet], wb_src[sheet]
        problems, inserted_total, previous = [], 0, 0
        for b in sorted(blocks, key=lambda x: x["parent_row"]):
            p, end, k = int(b["parent_row"]), int(b["block_end"]), int(b["count"])
            intruders = [r for r in lines if p < r <= end]
            if intruders:
                problems.append(f"Row {p}: another BOQ item (row {intruders[0]}) lies inside its block")
            p_new, end_new = _new_row(mapping, p), _new_row(mapping, end)
            if end_new - p_new != end - p:
                problems.append(f"Row {p}: rows were inserted inside the parent's own block")
            first = end_new + 1
            if first <= previous:
                problems.append(f"Row {p}: components are out of the original parent order")
            rows = list(range(first, first + k))
            previous = rows[-1]
            next_line = min((r for r in lines if r > end), default=None)
            # Whatever followed the block (a heading, the next BOQ item) follows the new rows directly.
            if next_line is not None and _new_row(mapping, next_line) - (rows[-1] + 1) != next_line - (end + 1):
                problems.append(f"Row {p}: its components are not directly above what followed the parent")
            for r in rows:
                cells = next(out.iter_rows(min_row=r, max_row=r, max_col=last_col), ())
                bad = [c.coordinate for c in cells if _fill(c) != ("solid", YELLOW)]
                if len(cells) < last_col or bad:
                    problems.append(f"Inserted row {r} is not yellow across the row ({', '.join(bad[:3]) or 'cells missing'})")
            inserted_total += k
            # The parent and the rows that already belonged to it keep their original fill.
            for r in range(p, end + 1):
                a = next(src.iter_rows(min_row=r, max_row=r, max_col=last_col), ())
                o = next(out.iter_rows(min_row=_new_row(mapping, r), max_row=_new_row(mapping, r), max_col=last_col), ())
                if [_fill(c) for c in a] != [_fill(c) for c in o][:len(a)]:
                    problems.append(f"Original row {r} changed its fill")
                    break
            if len(problems) > 20:
                break
        errors.extend(problems)
        checks["placement_verified"] = len(blocks)
        checks["inserted_rows_yellow"] = inserted_total if not problems else 0
    finally:
        wb_out.close()
        wb_src.close()
