"""Bulk historical ingestion: the Sales BOM variants found in real project folders, flat
component lists, Highside-only scope, folder scans (inventory, duplicates, unchanged and
changed files, retries) and the guarantee that source folders are never modified."""
import hashlib
import time
from pathlib import Path

import openpyxl
import pytest

from app.services.excel_processor.workbook import BOQ_REQUIRED
from app.services.knowledge.extract import extract_workbook

HEADER = ["Unique No", "ParentUniqueNo", "Cust Ref No.", "Item Brief", "Description", "Sales Item", "Cost Sheet Type",
          "Billable Qty", "Qty UOM", "ClassName", "Item No."]
assert set(BOQ_REQUIRED) <= set(HEADER)


def sales_bom(path: Path, rows: list[dict]) -> Path:
    """A Sales BOM worksheet in the company template; header on row 2 like the real files."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales BOM"
    ws["A1"] = "Do Not Edit"
    for c, h in enumerate(HEADER, start=1):
        ws.cell(2, c, h)
    for r, row in enumerate(rows, start=3):
        for c, h in enumerate(HEADER, start=1):
            if row.get(h) is not None:
                ws.cell(r, c, row[h])
    wb.create_sheet("Summary")["A1"] = "Totals only"
    wb.save(path)
    return path


def R(**kw):
    keys = {"uno": "Unique No", "pno": "ParentUniqueNo", "ref": "Cust Ref No.", "brief": "Item Brief",
            "desc": "Description", "sales": "Sales Item", "type": "Cost Sheet Type", "qty": "Billable Qty",
            "uom": "Qty UOM", "cls": "ClassName", "item": "Item No."}
    return {keys[k]: v for k, v in kw.items()}


def working_copy_rows():
    """Unique No still blank: the hierarchy is in the row order and Cust Ref No. (BOM 10 style)."""
    return [
        R(brief="PROJECT TITLE", sales="N", type="Section"),
        R(ref="1", brief="VRV System (Supply, Installation)", sales="N", type="Section"),
        R(ref="1.1", desc="Supply of VRV outdoor unit 38 HP RXQ38BRY16", sales="Y", qty=2, uom="NOS"),
        R(ref="1.1", brief="DAIKIN RXQ38BRY16 ODU UNIT", sales="N", qty=2, uom="NOS", cls="Component", item="CO-21405"),
        R(ref="1.1", brief="INSTALLATION OF ODU", sales="N", qty=2, uom="NOS", cls="Installation", item="CO-16001",
          type="Highside Installation"),
        R(ref="1.2", desc="Copper refrigerant piping 22 mm", sales="Y", qty=40, uom="RMT"),
        R(ref="1.2", brief="COPPER PIPE 22MM", sales="N", qty=40, uom="RMT", cls="Component", item="CO-90001",
          type="Lowside Supply"),
        R(ref="2", brief="OVERHEADS", sales="N", type="Section"),
        R(ref="2.1", brief="INSTALLATION OF MANPOWER", type="Mathadi", cls="Installation", item="IN-03374"),
    ]


def test_working_sales_bom_links_components_by_row_order(tmp_path):
    found = extract_workbook(sales_bom(tmp_path / "w.xlsm", working_copy_rows()))
    by_desc = {p.description: p for p in found.parents}
    odu = by_desc["Supply of VRV outdoor unit 38 HP RXQ38BRY16"]
    assert [c.item_no for c in odu.components] == ["CO-21405", "CO-16001"]
    assert odu.method == "row order" and odu.context[0] == "VRV System (Supply, Installation)"
    assert [c.row for c in odu.components] == [6, 7] and odu.row == 5
    assert odu.components[0].cost_sheet_type == ""  # left blank by the engineer: never invented
    pipe = by_desc["Copper refrigerant piping 22 mm"]
    assert [c.item_no for c in pipe.components] == ["CO-90001"]  # not merged into the ODU line
    # The overhead row sits directly under a section: reported, not attached to a wrong parent.
    assert found.orphans == 0 or all("IN-03374" not in [c.item_no for c in p.components] for p in found.parents)


def test_draft_with_blank_sales_item_uses_cost_sheet_type(tmp_path):
    """Vidyalankar style: Unique No hierarchy present, Sales Item blank on the lines."""
    rows = [
        R(uno="1", brief="SITE", type="Section"),
        R(uno="1.1", pno="1", brief="High Side", type="Section", uom="SECTION"),
        R(uno="1.1.1", pno="1.1", ref="1", brief="Supply of 52 HP outdoor unit", type="Highside Supply", qty=1, uom="NOS"),
        R(uno="1.1.1.1", pno="1.1.1", ref="1", brief="DAIKIN RXQ20BRY16 ODU", type="Highside Supply", qty=1,
          uom="NOS", cls="Component", item="CO-13424"),
    ]
    found = extract_workbook(sales_bom(tmp_path / "d.xlsm", rows))
    assert len(found.parents) == 1
    p = found.parents[0]
    assert p.description == "Supply of 52 HP outdoor unit" and p.method == "hierarchy"
    assert p.components[0].cost_sheet_type == "Highside Supply" and p.context == ["High Side"]


def test_flat_component_list_is_rejected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Index", "Item No", "Item Brief", "Description", "Total QTY", "UOM"])
    for i in range(12):
        ws.append([f"1.1.{i}.1", f"CO-1{i:04d}", f"AHU {i}", f"AHU {i} LPS", 1, "NOS"])
    path = tmp_path / "Book1.xlsx"
    wb.save(path)
    found = extract_workbook(path)
    assert not found.parents
    assert found.sheets[0]["note"].startswith("A flat list of components")


def test_damaged_styles_are_read_from_a_repaired_copy(tmp_path):
    import zipfile
    good = tmp_path / "good.xlsx"
    openpyxl_wb = openpyxl.Workbook()
    openpyxl_wb.active.append(["Description", "Qty"])
    openpyxl_wb.save(good)
    bad = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/styles.xml":
                data = (b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        b'<fills count="1"><fill/></fills></styleSheet>')  # openpyxl refuses an empty <fill/>
            dst.writestr(info, data)
    before = hashlib.sha256(bad.read_bytes()).hexdigest()
    found = extract_workbook(bad)
    assert found.repaired and hashlib.sha256(bad.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------- API: folder scans

def wait_scan(c, sid):
    for _ in range(600):
        s = c.get(f"/api/historical/scans/{sid}").json()
        if s["status"] in ("completed", "failed"):
            return s
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


@pytest.fixture(scope="module")
def api(sample_master):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_api_flow import wait

    with TestClient(app) as c:
        m = c.post("/api/uploads/item-master", files={"file": ("m.xlsx", sample_master.read_bytes())}).json()
        wait(lambda: next(x for x in c.get("/api/uploads/item-master").json() if x["id"] == m["id"]),
             lambda r: r["status"] == "ready")
        yield c


def digest_tree(root: Path) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_folder_scan_inventory_scope_dedup_and_change_detection(api, tmp_path):
    c = api
    root = tmp_path / "Projects"
    (root / "PGH 1" / "Project A").mkdir(parents=True)
    (root / "PGH 2" / "Project B" / "Nested").mkdir(parents=True)
    a = sales_bom(root / "PGH 1" / "Project A" / "A BOQ_IMPORT.xlsm", working_copy_rows())
    (root / "PGH 2" / "Project B" / "Nested" / "copy of A.xlsm").write_bytes(a.read_bytes())  # identical bytes
    (root / "PGH 1" / "Project A" / "~$A BOQ_IMPORT.xlsm").write_bytes(b"lock")
    (root / "PGH 1" / "Project A" / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0legacy")
    (root / "PGH 2" / "mail.msg").write_bytes(b"mail")
    wb = openpyxl.Workbook()
    wb.active.append(["Sr", "Description", "Qty", "Unit", "Rate"])
    wb.active.append(["1", "Supply of chiller", 1, "Nos", 100])
    wb.save(root / "PGH 2" / "Project B" / "client OC.xlsx")
    before = digest_tree(root)

    s = c.post("/api/historical/scans", json={"root_path": str(root)})
    assert s.status_code == 200, s.text
    s = wait_scan(c, s.json()["id"])
    assert s["status"] == "completed", s
    assert (s["folders_scanned"], s["files_discovered"], s["supported_files"]) == (6, 6, 3)
    assert (s["temporary_files"], s["unsupported_files"], s["duplicate_files"]) == (1, 2, 1)
    inv = {e["filename"]: e for e in s["inventory"]}
    assert inv["old.xls"]["status"] == "unsupported" and ".xls" in inv["old.xls"]["reason"]
    assert inv["copy of A.xlsm"]["status"] == "duplicate"
    assert inv["client OC.xlsx"]["status"] == "no_mappings"
    hid = inv["A BOQ_IMPORT.xlsm"]["historical_id"]
    h = c.get(f"/api/historical/{hid}").json()
    # Highside only: the ODU line is learned; the Lowside pipe and the overhead row are not.
    assert h["status"] in ("ingested", "partial")
    assert h["parents_count"] == 1 and h["components_count"] == 2 and h["out_of_scope_count"] >= 1
    assert h["relative_path"].endswith("A BOQ_IMPORT.xlsm") and h["folder"] == str(Path("PGH 1") / "Project A")
    maps = c.get(f"/api/historical/{hid}/mappings").json()["items"]
    assert len(maps) == 1 and {x["resolved_cost_sheet_type"] for x in maps[0]["components"]} == \
        {"Highside Supply", "Highside Installation"}
    blank = next(x for x in maps[0]["components"] if x["item_no"] == "CO-21405")
    assert blank["type_source"] == "item_master" and blank["row_number"] == 6
    found = c.get("/api/knowledge/mappings", params={"item_no": "CO-16001"}).json()
    assert found["total"] >= 1 and found["items"][0]["source"]["relative_path"].endswith("A BOQ_IMPORT.xlsm")
    stats = c.get("/api/knowledge/stats").json()
    assert stats["latest_scan"]["files_discovered"] == 6 and stats["highside_installation"] >= 1

    # Rescan: nothing changed, nothing is processed again.
    s2 = wait_scan(c, c.post("/api/historical/scans", json={"root_path": str(root)}).json()["id"])
    assert s2["to_process"] == 0 and s2["unchanged_files"] >= 2

    # A changed file is detected and reprocessed in place: no duplicate mappings.
    rows = working_copy_rows()
    rows[2]["Description"] = "Supply of VRV outdoor unit 38 HP RXQ38BRY16 (revised)"
    sales_bom(a, rows)
    after_edit = digest_tree(root)
    s3 = wait_scan(c, c.post("/api/historical/scans", json={"root_path": str(root)}).json()["id"])
    assert s3["changed_files"] == 1 and s3["to_process"] == 1
    maps = c.get(f"/api/historical/{hid}/mappings").json()["items"]
    assert len(maps) == 1 and maps[0]["description"].endswith("(revised)")
    assert digest_tree(root) == after_edit  # scanning never writes to the source folder
    assert before.keys() == after_edit.keys()


def test_unreadable_file_fails_and_retry_picks_it_up(api, tmp_path):
    c = api
    root = tmp_path / "Locked"
    root.mkdir()
    path = sales_bom(root / "locked.xlsm", working_copy_rows()[:5])
    s = wait_scan(c, c.post("/api/historical/scans", json={"root_path": str(root)}).json()["id"])
    hid = s["inventory"][0]["historical_id"]
    # Simulate the file having been locked during the scan.
    from app.database.session import SessionLocal
    from app.models.tables import HistoricalBOQ
    with SessionLocal() as db:
        h = db.get(HistoricalBOQ, hid)
        h.status, h.error, h.sha256, h.stored_path = "failed", "locked", "", ""
        db.commit()
    assert c.post("/api/historical/retry-failed").json()["queued"] >= 1
    for _ in range(300):
        h = c.get(f"/api/historical/{hid}").json()
        if h["status"] not in ("queued", "processing"):
            break
        time.sleep(0.1)
    assert h["status"] in ("ingested", "partial") and h["sha256"] and path.exists()


def test_scan_rejects_missing_folder(api, tmp_path):
    r = api.post("/api/historical/scans", json={"root_path": str(tmp_path / "nope")})
    assert r.status_code == 400
