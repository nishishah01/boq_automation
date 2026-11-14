"""BOQs that are not in the Sales BOM template: detection, parsing, matching and export."""
import zipfile

import openpyxl
import pytest

from app.services.boq_parser.layout import assign_roles, detect_layout, validate_layout
from app.services.boq_parser.parser import parse_boq
from app.services.excel_export.exporter import ChildPlan, export_boq
from app.services.excel_export.verifier import verify_export
from app.services.excel_processor.workbook import WorkbookError, inspect_boq


def client_boq(path, header=("Sl. No.", "Description of Work", "Unit", "Qty", "Rate", "Amount"), sheet="Price Schedule"):
    """A typical client BOQ: title rows, its own column names, headings, specs and totals."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cover"
    ws["A1"] = "Tender for HVAC works"
    ws = wb.create_sheet(sheet)
    ws["A1"] = "ABC Hospital, Pune"
    ws["A2"] = "Bill of Quantities - HVAC"
    for i, h in enumerate(header, start=1):
        ws.cell(4, i, h)
    body = [
        ("A", "HVAC WORKS", None, None, None),
        ("1", "Supply of VRV indoor One way Cassette units of the following capacities including all accessories", None, None, None),
        ("1.1", "2.01 TR FXKQ63ARV16", "Nos", 2, 85000),
        ("1.2", "1.59 TR FXKQ50ARV16", "Nos", 3, 76000),
        ("", "Make: Daikin", None, None, None),
        ("2", "Supply of VRV outdoor units", None, None, None),
        ("2.1", "38 HP RXQ38BRY16", "Set", 1, 950000),
    ]
    r = 5
    for sl, desc, unit, qty, rate in body:
        ws.cell(r, 1, sl)
        ws.cell(r, 2, desc)
        ws.cell(r, 3, unit)
        ws.cell(r, 4, qty)
        ws.cell(r, 5, rate)
        if qty:
            ws.cell(r, 6, f"=D{r}*E{r}")
        r += 1
    ws.cell(r, 2, "Sub Total")
    ws.cell(r, 6, f"=SUM(F5:F{r - 1})")
    wb.save(path)
    return path


@pytest.fixture
def generic(tmp_path):
    return client_boq(tmp_path / "client.xlsx")


def test_detects_header_row_and_columns(generic):
    info = inspect_boq(generic)
    assert info["selected_sheet"] == "Price Schedule"
    lay = next(s for s in info["sheets"] if s["name"] == "Price Schedule")["layout"]
    assert lay["format"] == "generic" and lay["header_row"] == 4 and lay["confidence"] == "high"
    assert lay["columns"] == {"serial": 0, "description": 1, "unit": 2, "quantity": 3, "rate": 4, "amount": 5}
    assert not lay["needs_mapping"]


@pytest.mark.parametrize("name", ["Description", "Item Description", "BOQ Description", "Item Brief",
                                  "Scope of Work", "Detailed Description", "Particulars", "DESCRIPTION OF ITEMS"])
def test_description_header_variants(name):
    assert assign_roles(["S.No", name, "Qty"])["description"][0] == 1


def test_description_preferred_over_item_brief():
    assert assign_roles(["Item Brief", "Description", "Qty"])["description"][0] == 1


def test_parents_and_specs(generic):
    parsed = parse_boq(generic, "Price Schedule")
    texts = [l.row.text for l in parsed.lines]
    assert texts == ["2.01 TR FXKQ63ARV16", "1.59 TR FXKQ50ARV16; Make: Daikin", "38 HP RXQ38BRY16"]
    first = parsed.lines[0]
    assert first.context[0].startswith("Supply of VRV indoor One way Cassette")
    assert first.context[1] == "HVAC WORKS"
    assert parsed.lines[2].context == ["Supply of VRV outdoor units", "HVAC WORKS"]  # not under the cassettes
    assert first.row.billable_qty == 2 and first.row.uom == "Nos" and first.row.unique_no == "1.1"


def test_unclear_description_asks_for_mapping(tmp_path):
    path = client_boq(tmp_path / "odd.xlsx", header=("Ref", "Text", "Measure", "Count", "Price", "Value"))
    lay = detect_layout(openpyxl.load_workbook(path)["Price Schedule"])
    assert lay["needs_mapping"] and lay["confidence"] != "high"
    assert lay["columns"].get("description") == 1  # suggested from the longest text


def test_no_description_is_a_clear_error():
    errors = validate_layout({"format": "generic", "header_row": 4, "columns": {"quantity": 3}})
    assert errors and "description column is mandatory" in errors[0]
    with pytest.raises(WorkbookError):
        from app.services.boq_parser.parser import parse_generic
        parse_generic(None, {"format": "generic", "header_row": 4, "columns": {}})


def test_sales_bom_still_detected(sample_boq):
    info = inspect_boq(sample_boq)
    assert info["selected_sheet"] == "Sales BOM" and info["has_macros"]
    assert next(s for s in info["sheets"] if s["name"] == "Sales BOM")["layout"]["format"] == "sales_bom"


def test_untyped_lines_are_matched(engine, generic):
    parsed = parse_boq(generic, "Price Schedule")
    r = engine.match_line(parsed.lines[0])
    assert r.in_scope and [s.component.item_no for s in r.selections] == ["CO-01383"]


def _plan(indexes, row, item_no, cost_sheet_type=None):
    m = indexes.by_no[item_no]
    return ChildPlan(row, m.item_no, m.item_brief, cost_sheet_type or m.cost_sheet_type, m.class_name, m.cost)


def test_export_inserts_components_below_their_item(generic, tmp_path, indexes):
    parsed = parse_boq(generic, "Price Schedule")
    first, second = parsed.lines[0].row.row, parsed.lines[1].row.row  # rows 7 and 8 (8 has a spec row, 9)
    plans = [_plan(indexes, first, "CO-21405"), _plan(indexes, second, "CO-01383"),
             _plan(indexes, second, "CO-21405", "Highside Installation")]
    out = tmp_path / "out.xlsx"
    info = export_boq(generic, "Price Schedule", plans, out, parsed.layout)
    result = verify_export(generic, out, "Price Schedule", info, indexes.by_no)
    assert result["ok"], result["errors"]

    ws = openpyxl.load_workbook(out)["Price Schedule"]
    assert [ws.cell(4, c).value for c in (7, 8)] == ["Component Item No.", "Cost Sheet Type"]
    # Directly below the first item; below the second item's specification row.
    assert [ws.cell(8, c).value for c in (2, 7, 8)] == [indexes.by_no["CO-21405"].item_brief, "CO-21405",
                                                        "Highside Supply"]
    assert ws.cell(9, 2).value == "1.59 TR FXKQ50ARV16" and ws.cell(10, 2).value == "Make: Daikin"
    assert [ws.cell(r, 7).value for r in (11, 12)] == ["CO-01383", "CO-21405"]
    assert ws.cell(12, 8).value == "Highside Installation"  # the reviewed type, not the master's
    assert ws.cell(14, 2).value == "38 HP RXQ38BRY16"
    # Formulas follow their rows and the total still covers every item.
    assert ws.cell(9, 6).value == "=D9*E9" and ws.cell(14, 6).value == "=D14*E14"
    assert ws.cell(15, 6).value == "=SUM(F5:F14)"
    # The whole new row is yellow; original rows keep their own fill.
    assert all(ws.cell(8, c).fill.fgColor.rgb == "FFFFFF00" for c in range(1, 9))
    assert ws.cell(7, 2).fill.fgColor.rgb != "FFFFFF00"

    # Re-reading the export recognises the component rows as children, not BOQ items.
    again = parse_boq(out, "Price Schedule")
    assert again.layout["columns"]["mapped_item_no"] == 6 and len(again.lines) == 3
    assert [c.item_no for c in again.lines[0].children] == ["CO-21405"]
    assert [(c.item_no, c.cost_sheet_type) for c in again.lines[1].children] == [
        ("CO-01383", "Highside Supply"), ("CO-21405", "Highside Installation")]


def test_insertion_moves_every_reference(generic, tmp_path, indexes):
    """Other sheets, names, merges, validations and conditional formats follow the rows."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.load_workbook(generic)
    ws = wb["Price Schedule"]
    ws.merge_cells("A13:F13")
    ws["A13"] = "Notes below the total"
    dv = DataValidation(type="list", formula1='"Nos,Set,Rmt"')
    dv.add("C7:C11")
    ws.add_data_validation(dv)
    ws.conditional_formatting.add("F5:F11", CellIsRule(operator="greaterThan", formula=["$E7*0"], fill=None))
    wb.defined_names["Items"] = DefinedName("Items", attr_text="'Price Schedule'!$B$7:$B$11")
    summary = wb.create_sheet("Summary")
    summary["A1"] = "Total"
    summary["B1"] = "='Price Schedule'!F12"
    summary["B2"] = "=SUM('Price Schedule'!F7:F8)"
    src = tmp_path / "refs.xlsx"
    wb.save(src)

    parsed = parse_boq(src, "Price Schedule")
    out = tmp_path / "out.xlsx"
    info = export_boq(src, "Price Schedule", [_plan(indexes, parsed.lines[0].row.row, "CO-21405")], out, parsed.layout)
    result = verify_export(src, out, "Price Schedule", info, indexes.by_no)
    assert result["ok"], result["errors"]

    wb = openpyxl.load_workbook(out)
    ws = wb["Price Schedule"]
    assert wb["Summary"]["B1"].value == "='Price Schedule'!F13"
    assert wb["Summary"]["B2"].value == "=SUM('Price Schedule'!F7:F9)"  # range grows across the new row
    assert wb.defined_names["Items"].attr_text == "'Price Schedule'!$B$7:$B$12"
    assert "A14:F14" in [str(r) for r in ws.merged_cells.ranges]
    with zipfile.ZipFile(out) as z:
        sheet = z.read(info["sheet_part"]).decode()
    assert 'sqref="C7:C12"' in sheet and 'sqref="F5:F12"' in sheet


def test_added_columns_follow_the_data_not_blank_formatting(generic, tmp_path, indexes):
    # Google Sheets exports format every cell up to column Z. Added columns must still go
    # right after the last column with data (F), not after Z, and get readable widths.
    wb = openpyxl.load_workbook(generic)
    ws = wb["Price Schedule"]
    for r in range(1, ws.max_row + 1):
        for c in range(7, 27):
            ws.cell(r, c).fill = openpyxl.styles.PatternFill("solid", fgColor="FFEEEEEE")
    for c in range(7, 27):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = 8
    src = tmp_path / "gsheets.xlsx"
    wb.save(src)

    parsed = parse_boq(src, "Price Schedule")
    out = tmp_path / "out.xlsx"
    info = export_boq(src, "Price Schedule", [_plan(indexes, parsed.lines[0].row.row, "CO-21405")], out, parsed.layout)
    assert info["added_columns"] == {"G": "Component Item No.", "H": "Cost Sheet Type"}
    assert verify_export(src, out, "Price Schedule", info, indexes.by_no)["ok"]
    ws = openpyxl.load_workbook(out)["Price Schedule"]
    assert ws.cell(8, 7).value == "CO-21405"
    assert ws.column_dimensions["G"].width == 18 and ws.column_dimensions["I"].width == 8


def test_existing_component_columns_are_reused(tmp_path, indexes):
    """A BOQ that already has Cost Sheet Type and component number columns gets no new columns."""
    path = client_boq(tmp_path / "cols.xlsx", header=("Sl. No.", "Description of Work", "Unit", "Qty", "Rate",
                                                      "Amount", "Cost Sheet Type", "Component Item No"))
    parsed = parse_boq(path, "Price Schedule")
    out = tmp_path / "out.xlsx"
    info = export_boq(path, "Price Schedule", [_plan(indexes, parsed.lines[0].row.row, "CO-21405")], out, parsed.layout)
    assert info["added_columns"] == {} and info["columns"]["mapped_item_no"] == "H"
    assert verify_export(path, out, "Price Schedule", info, indexes.by_no)["ok"]
    ws = openpyxl.load_workbook(out)["Price Schedule"]
    assert (ws.cell(8, 7).value, ws.cell(8, 8).value) == ("Highside Supply", "CO-21405")


def test_export_keeps_macros_in_xlsm(generic, tmp_path, indexes, sample_boq):
    # An .xlsm client BOQ: the macro part must come through byte-identical.
    with zipfile.ZipFile(sample_boq) as z:
        vba = z.read("xl/vbaProject.bin")
    src = tmp_path / "client.xlsm"
    wb = openpyxl.load_workbook(generic)
    wb.save(src)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("xl/vbaProject.bin", vba)
    parsed = parse_boq(src, "Price Schedule")
    out = tmp_path / "out.xlsm"
    plans = [_plan(indexes, parsed.lines[0].row.row, "CO-21405")]
    info = export_boq(src, "Price Schedule", plans, out, parsed.layout)
    result = verify_export(src, out, "Price Schedule", info, indexes.by_no)
    assert result["ok"] and result["checks"]["vba_identical"], result["errors"]
    assert info["added_rows"] == [8]


def test_api_upload_map_run_and_export(tmp_path, sample_master):
    """A client BOQ with unclear headers: upload succeeds, a run is refused until the columns are
    confirmed, then matching and export work in the client's own layout."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_api_flow import wait

    path = client_boq(tmp_path / "odd.xlsx", header=("Ref", "Text", "Measure", "Count", "Price", "Value"))
    with TestClient(app) as c:
        b = c.post("/api/uploads/boq", files={"file": ("odd.xlsx", path.read_bytes())})
        assert b.status_code == 200, b.text
        b = b.json()
        lay = next(s for s in b["sheets"] if s["name"] == b["selected_sheet"])["layout"]
        assert b["selected_sheet"] == "Price Schedule" and lay["needs_mapping"]

        m = c.post("/api/uploads/item-master", files={"file": ("m.xlsx", sample_master.read_bytes())}).json()
        wait(lambda: next(x for x in c.get("/api/uploads/item-master").json() if x["id"] == m["id"]),
             lambda r: r["status"] == "ready")
        body = {"boq_upload_id": b["id"], "item_master_upload_id": m["id"], "cost_sheet_types": ["Highside Supply"]}
        refused = c.post("/api/jobs", json=body)
        assert refused.status_code in (409, 422) and "unit" in refused.json()["detail"]

        bad = c.put(f"/api/uploads/boq/{b['id']}/layout", json={"sheet": "Price Schedule", "header_row": 4,
                                                                "columns": {"quantity": 3}})
        assert bad.status_code == 422 and "description column is mandatory" in bad.json()["detail"]

        detected = c.get(f"/api/uploads/boq/{b['id']}/layout", params={"sheet": "Price Schedule", "header_row": 4}).json()
        assert detected["headers"][:2] == ["Ref", "Text"]
        ok = c.put(f"/api/uploads/boq/{b['id']}/layout", json={"sheet": "Price Schedule", "header_row": 4, "columns": {
            "serial": 0, "description": 1, "unit": 2, "quantity": 3, "rate": 4, "amount": 5}})
        assert ok.status_code == 200, ok.text
        assert ok.json()["summary"]["lines"] == 3

        j = c.post("/api/jobs", json=body).json()
        j = wait(lambda: c.get(f"/api/jobs/{j['id']}").json(), lambda r: r["status"] in ("completed", "failed"))
        assert j["status"] == "completed", j["error"]
        descs = c.get(f"/api/jobs/{j['id']}/descriptions").json()
        cassette = next(d for d in descs if "FXKQ63" in d["description"])
        assert [x["item_no"] for x in cassette["mappings"]] == ["CO-01383"]
        c.post(f"/api/mappings/{cassette['mappings'][0]['id']}/approve")
        rec = c.post(f"/api/jobs/{j['id']}/export").json()
        assert rec["status"] == "verified", rec["verification"]
        out = c.get(f"/api/exports/{rec['id']}/download/boq")
        assert out.status_code == 200


def test_google_sheets_export_with_two_row_header(tmp_path):
    """Google Sheets omits the sheet size record; quantity is split over merged sub-columns;
    zero-quantity items are still items; paragraphs do not nest deeper and deeper."""
    import re as _re
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Part1-VRF System"
    ws["A2"] = "Project : Test"
    for c, h in enumerate(["Sr. No.", "Description of Works", "Quantity", None, None, "Unit", "Supply\nRate"], start=1):
        ws.cell(7, c, h)
    for c, h in zip((3, 4, 5), ("Level 7", "Level 13", "Total")):
        ws.cell(8, c, h)
    ws.merge_cells("C7:E7")
    data = [("1.0", "VRF / VRV SYSTEM (HI SIDE)"), (None, "Supply of Outdoor Units suitable for R410A"),
            (None, "The Outdoor units shall be compact."), (None, "Top Discharge type Air-cooled Outdoor unit"),
            ("1.1.1", "20 HP VRF ODU", 0, 0, 0, "Set"), ("1.1.2", "18 HP VRF ODU", 0, 16, 16, "Sets"),
            ("2.0", "INDOOR UNITS")]
    for r, row in enumerate(data, start=10):
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
    path = tmp_path / "gsheet.xlsx"
    wb.save(path)
    with zipfile.ZipFile(path) as z:  # drop <dimension>, as Google Sheets does
        parts = {n: z.read(n) for n in z.namelist()}
    parts["xl/worksheets/sheet1.xml"] = _re.sub(rb"<dimension[^>]*/>", b"", parts["xl/worksheets/sheet1.xml"])
    with zipfile.ZipFile(path, "w") as z:
        for n, d in parts.items():
            z.writestr(n, d)

    info = inspect_boq(path)
    lay = info["sheets"][0]["layout"]
    assert info["sheets"][0]["rows"] > 1 and lay["header_row"] == 7 and lay["confidence"] == "high"
    assert lay["headers"][lay["columns"]["quantity"]] == "Quantity Total"
    lines = parse_boq(path, "Part1-VRF System", lay).lines
    assert [(l.row.text, l.row.billable_qty) for l in lines] == [("20 HP VRF ODU", 0), ("18 HP VRF ODU", 16)]
    assert lines[0].context == ["Top Discharge type Air-cooled Outdoor unit", "VRF / VRV SYSTEM (HI SIDE)"]


# ---------------- unit and quantity rule ----------------

from app.services.boq_parser import eligibility as E  # noqa: E402


@pytest.mark.parametrize("unit,qty,expected", [
    ("Nos", 2, True), ("Nos", None, False), ("", 2, False), ("", None, False),
    ("-", 2, False), ("NA", 2, False), ("12", 2, False),          # not units
    ("Nos", "LS", False), ("Nos", "-", False), ("Nos", -1, False),  # not quantities
    ("Nos", "1,250", True), ("Rmt", 12.5, True),
])
def test_unit_and_quantity_both_required(unit, qty, expected):
    state, _ = E.quantity_state(qty)
    assert E.check(unit, state, str(qty)).eligible is expected


def test_zero_quantity_is_configurable():
    review = E.check("Nos", "zero", "0", "review")
    assert review.eligible and review.flag == E.ZERO_FLAG
    skip = E.check("Nos", "zero", "0", "skip")
    assert not skip.eligible and "quantity is 0" in skip.reason


def test_each_row_is_checked_on_its_own(tmp_path):
    """A row's missing unit is never borrowed from the row above."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for c, h in enumerate(["S.No", "Description", "Unit", "Qty"], start=1):
        ws.cell(1, c, h)
    rows = [("1", "Supply of VRV units"), ("1.1", "2.01 TR FXKQ63ARV16", "Nos", 2),
            ("1.2", "1.59 TR FXKQ50ARV16", None, 3), ("1.3", "38 HP RXQ38BRY16", "Set", None),
            ("1.4", "1.02 TR FXAQ32ARV16", "Nos", "LS"), ("1.5", "0.82 TR cassette", "Nos", 0)]
    for r, row in enumerate(rows, start=2):
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
    path = tmp_path / "u.xlsx"
    wb.save(path)
    lines = parse_boq(path, ws.title).lines
    got = {l.row.text: E.check(l.row.uom, l.row.qty_state, l.row.qty_raw).eligible for l in lines}
    assert got == {"2.01 TR FXKQ63ARV16": True, "1.59 TR FXKQ50ARV16": False, "38 HP RXQ38BRY16": False,
                   "1.02 TR FXAQ32ARV16": False, "0.82 TR cassette": True}


def test_skipped_lines_get_nothing(tmp_path, sample_master):
    """End to end: skipped lines are never matched, cannot be given components, and export nothing."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_api_flow import wait

    wb = openpyxl.Workbook()
    ws = wb.active
    for c, h in enumerate(["S.No", "Description", "Unit", "Qty"], start=1):
        ws.cell(1, c, h)
    for r, row in enumerate([("1", "2.01 TR FXKQ63ARV16", "Nos", 2), ("2", "1.59 TR FXKQ50ARV16", None, 3),
                             ("3", "1.02 TR FXAQ32ARV16", "Nos", 0)], start=2):
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
    path = tmp_path / "skip.xlsx"
    wb.save(path)
    with TestClient(app) as c:
        b = c.post("/api/uploads/boq", files={"file": ("skip.xlsx", path.read_bytes())}).json()
        m = c.post("/api/uploads/item-master", files={"file": ("m.xlsx", sample_master.read_bytes())}).json()
        wait(lambda: next(x for x in c.get("/api/uploads/item-master").json() if x["id"] == m["id"]),
             lambda r: r["status"] == "ready")
        j = c.post("/api/jobs", json={"boq_upload_id": b["id"], "item_master_upload_id": m["id"],
                                      "cost_sheet_types": ["Highside Supply"]}).json()
        j = wait(lambda: c.get(f"/api/jobs/{j['id']}").json(), lambda r: r["status"] in ("completed", "failed"))
        assert j["summary"]["eligibility"] == {"identified": 3, "eligible": 2, "skipped_unit_qty": 1,
                                               "zero_quantity": 1, "zero_quantity_rule": "review"}
        descs = {d["description"]: d for d in c.get(f"/api/jobs/{j['id']}/descriptions").json()}
        skipped = descs["1.59 TR FXKQ50ARV16"]
        assert skipped["status"] == "incomplete" and skipped["mappings"] == [] and skipped["candidates"] == []
        assert skipped["skip_reason"].startswith("Skipped – Unit/Quantity Missing")
        assert c.post(f"/api/descriptions/{skipped['id']}/mappings", json={"item_no": "CO-01378"}).status_code == 409
        assert c.post(f"/api/descriptions/{skipped['id']}/regenerate").status_code == 409

        zero = descs["1.02 TR FXAQ32ARV16"]
        assert zero["mappings"] and all(x["status"] == "review" and E.ZERO_FLAG in x["flags"] for x in zero["mappings"])

        for d in descs.values():
            for x in d["mappings"]:
                c.post(f"/api/mappings/{x['id']}/approve")
        rec = c.post(f"/api/jobs/{j['id']}/export").json()
        assert rec["status"] == "verified", rec["verification"]
        assert rec["summary"]["descriptions_skipped_unit_qty"] == 1
        out = tmp_path / "out.xlsx"
        out.write_bytes(c.get(f"/api/exports/{rec['id']}/download/boq").content)
        ws = openpyxl.load_workbook(out).active
        # Row 2's component sits directly below it; the skipped item (now row 4) gets nothing.
        assert ws.cell(1, 5).value == "Component Item No." and ws.cell(1, 6).value == "Cost Sheet Type"
        assert ws.cell(3, 5).value == "CO-01383" and ws.cell(3, 6).value == "Highside Supply"
        assert ws.cell(4, 2).value == "1.59 TR FXKQ50ARV16" and ws.cell(5, 5).value is None
        assert ws.cell(3, 1).fill.fgColor.rgb == "FFFFFF00" and ws.cell(2, 1).fill.fgColor.rgb != "FFFFFF00"
