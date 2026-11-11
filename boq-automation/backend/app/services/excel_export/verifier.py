"""Verify an exported workbook before it can be downloaded. Any failure blocks the download."""
import hashlib
import warnings
import zipfile
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", module="openpyxl")

# Only these parts may differ from the source workbook.
EXPECTED_CHANGES = {"xl/sharedStrings.xml", "xl/workbook.xml", "[Content_Types].xml", "xl/_rels/workbook.xml.rels"}
EXPECTED_REMOVED = {"xl/calcChain.xml"}
MUST_SURVIVE_IN_SHEET = ["<sheetProtection", "<conditionalFormatting", "<drawing ", "x14:dataValidation",
                         "<autoFilter", "<pane "]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_export(source: str | Path, output: str | Path, sheet: str, info: dict, item_master: dict) -> dict:
    """item_master maps Item No -> Component. Returns {ok, checks, errors}."""
    errors: list[str] = []
    checks: dict[str, object] = {}

    with zipfile.ZipFile(source) as a, zipfile.ZipFile(output) as b:
        names_a, names_b = set(a.namelist()), set(b.namelist())
        if names_a - names_b - EXPECTED_REMOVED:
            errors.append(f"Parts lost: {sorted(names_a - names_b - EXPECTED_REMOVED)}")
        if names_b - names_a:
            errors.append(f"Unexpected parts: {sorted(names_b - names_a)}")
        allowed = EXPECTED_CHANGES | {info["sheet_part"]}
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

    src = openpyxl.load_workbook(source, read_only=True, data_only=True)[sheet]
    src_rows = {i: tuple(r) for i, r in enumerate(src.iter_rows(values_only=True), start=1)}
    out_ws = openpyxl.load_workbook(output, data_only=True)[sheet]
    out_rows = {i: tuple(r) for i, r in enumerate(out_ws.iter_rows(values_only=True), start=1)}

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
        for name, want in (("Cost Sheet Type", master.cost_sheet_type), ("Item No.", master.item_no),
                           ("Item Brief", master.item_brief), ("ClassName", e["class_name"]),
                           ("ParentUniqueNo", e["parent_no"]), ("Sales Item", "N"), ("Qty UOM", e["uom"])):
            got = col(r, name)
            if got != want:
                errors.append(f"{e['unique_no']}: {name} is {got!r}, expected {want!r}")
        if (e["parent_no"], e["item_no"]) in pairs:
            errors.append(f"Duplicate {e['item_no']} under {e['parent_no']}")
        pairs.add((e["parent_no"], e["item_no"]))
    checks["imported_rows_verified"] = len(info["expected"])
    return {"ok": not errors, "checks": checks, "errors": errors}
