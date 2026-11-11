"""End to end through the HTTP API: upload, process, review, export, download."""
import io
import time
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait(fn, done, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if done(r):
            return r
        time.sleep(0.2)
    raise AssertionError("timed out")


@pytest.fixture(scope="module")
def job(client, sample_boq, sample_master):
    b = client.post("/api/uploads/boq", files={"file": ("sample_boq.xlsm", sample_boq.read_bytes())}).json()
    assert b["selected_sheet"] == "Sales BOM" and b["has_macros"]
    m = client.post("/api/uploads/item-master", files={"file": ("m.xlsx", sample_master.read_bytes())}).json()
    wait(lambda: next(x for x in client.get("/api/uploads/item-master").json() if x["id"] == m["id"]),
         lambda r: r["status"] == "ready")
    j = client.post("/api/jobs", json={"boq_upload_id": b["id"], "item_master_upload_id": m["id"],
                                       "cost_sheet_types": ["Highside Supply", "Highside Installation"]}).json()
    wait(lambda: client.get(f"/api/jobs/{j['id']}").json(), lambda r: r["status"] in ("completed", "failed"))
    final = client.get(f"/api/jobs/{j['id']}").json()
    assert final["status"] == "completed", final["error"]
    return final


def desc(client, job, uno):
    return next(d for d in client.get(f"/api/jobs/{job['id']}/descriptions").json() if d["unique_no"] == uno)


def test_rejects_wrong_extension(client):
    assert client.post("/api/uploads/boq", files={"file": ("a.txt", b"hello")}).status_code == 400


def test_rejects_corrupt_workbook(client):
    assert client.post("/api/uploads/boq", files={"file": ("a.xlsx", b"PKnot really")}).status_code == 422


def test_rejects_workbook_without_sales_bom_columns(client):
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    wb.save(buf)
    assert client.post("/api/uploads/boq", files={"file": ("a.xlsx", buf.getvalue())}).status_code == 422


def test_lowside_type_is_refused_by_default(client, job):
    r = client.post("/api/jobs", json={"boq_upload_id": job["boq_upload_id"], "item_master_upload_id": job["item_master_upload_id"],
                                       "cost_sheet_types": ["Lowside Supply"]})
    assert r.status_code == 400


def test_job_statuses(client, job):
    descs = client.get(f"/api/jobs/{job['id']}/descriptions").json()
    assert job["processed"] == job["total"] == len(descs)
    by = {d["unique_no"]: d for d in descs}
    assert by["1.1.1.1"]["status"] == "mapped" and by["1.9.1"]["status"] == "skipped"
    assert by["1.8.1"]["status"] == "unmatched"


def test_nothing_is_auto_approved(client, job):
    for d in client.get(f"/api/jobs/{job['id']}/descriptions").json():
        assert all(m["status"] in ("pending", "review") for m in d["mappings"])


def test_low_confidence_needs_individual_approval(client, job):
    d = desc(client, job, "1.6.3")
    assert d["mappings"][0]["status"] == "review"
    after = client.post(f"/api/descriptions/{d['id']}/approve-all").json()
    assert after["mappings"][0]["status"] == "review"


def test_manual_item_must_exist_and_be_in_scope(client, job):
    d = desc(client, job, "1.8.1")
    assert client.post(f"/api/descriptions/{d['id']}/mappings", json={"item_no": "CO-99999"}).status_code == 400
    assert client.post(f"/api/descriptions/{d['id']}/mappings", json={"item_no": "CO-10880"}).status_code == 400


def test_duplicate_manual_add_is_refused(client, job):
    d = desc(client, job, "1.3.1")
    assert client.post(f"/api/descriptions/{d['id']}/mappings", json={"item_no": "CO-16804"}).status_code == 409


def test_search_filters_by_cost_sheet_type(client, job):
    r = client.get(f"/api/item-master/{job['item_master_upload_id']}/search",
                   params={"q": "daikin rxq38", "cost_sheet_type": "Highside Supply"}).json()
    assert [i["item_no"] for i in r["items"]] == ["CO-21405"]


def test_search_in_scope_types_is_filtered_before_paging(client, job):
    # Lowside insulation rows sort first; a browser-side filter on one page finds nothing.
    r = client.get(f"/api/item-master/{job['item_master_upload_id']}/search",
                   params={"q": "insulation", "types": "Highside Supply,Highside Installation", "limit": 25}).json()
    assert "CO-19622" in [i["item_no"] for i in r["items"]]
    assert all(i["cost_sheet_type"].startswith("Highside") for i in r["items"])


def test_item_detail_returns_original_record(client, job):
    r = client.get(f"/api/item-master/{job['item_master_upload_id']}/items/CO-21405").json()
    assert r["raw"]["Item Brief"] == "DAIKIN RXQ38BRY16 ODU UNIT"


def test_replace_remove_flag_and_audit(client, job):
    d = desc(client, job, "1.3.2")
    m = d["mappings"][0]
    r = client.post(f"/api/mappings/{m['id']}/replace", json={"item_no": "CO-16804", "note": "client wants 8 TR"}).json()
    assert r["item_no"] == "CO-16804" and r["source"] == "manual" and r["status"] == "approved"
    client.post(f"/api/descriptions/{d['id']}/flag", json={"note": "confirm with client"})
    client.delete(f"/api/mappings/{m['id']}")
    actions = [a for a in client.get(f"/api/jobs/{job['id']}/audit").json() if a["description_id"] == d["id"]]
    assert {"replace", "flag", "remove"} <= {a["action"] for a in actions}
    assert next(a for a in actions if a["action"] == "replace")["previous_item_no"] == "CO-16807"
    assert desc(client, job, "1.3.2")["status"] == "unmatched"


def test_regenerate_learns_from_a_replacement(client, job):
    # The engineer replaced CO-16807 on this line, so regenerating must not suggest it again.
    d = desc(client, job, "1.3.2")
    r = client.post(f"/api/descriptions/{d['id']}/regenerate").json()
    assert "CO-16807" not in [m["item_no"] for m in r["mappings"]]
    rejected = [c for c in r["candidates"] if c["item_no"] == "CO-16807"]
    assert rejected and rejected[0]["decision"] == "rejected"
    assert any("marked this wrong" in x for x in rejected[0]["conflicts"])
    client.post(f"/api/descriptions/{d['id']}/flag", json={"flagged": False})


def test_edited_requirements_drive_regeneration(client, job):
    d = desc(client, job, "1.3.3")  # 6 TR has no record until the engineer corrects it
    assert not d["mappings"]
    client.patch(f"/api/descriptions/{d['id']}/requirements", json={"specification": "8 TR", "engineer_note": ""})
    r = client.post(f"/api/descriptions/{d['id']}/regenerate").json()
    assert r["requirements_edited"] and [m["item_no"] for m in r["mappings"]] == ["CO-16804"]


def test_export_only_contains_approved(client, job):
    d = desc(client, job, "1.1.2.1")
    client.post(f"/api/mappings/{d['mappings'][0]['id']}/approve")
    pending = desc(client, job, "1.1.2.2")
    rec = client.post(f"/api/jobs/{job['id']}/export").json()
    assert rec["status"] == "verified", rec["verification"]
    assert rec["summary"]["pending_mappings"] > 0
    data = client.get(f"/api/exports/{rec['id']}/download/boq").content
    ws = openpyxl.load_workbook(io.BytesIO(data), data_only=True)["Sales BOM"]
    numbers = {r[0]: r for r in ws.iter_rows(min_row=3, values_only=True)}
    assert numbers["1.1.2.1.1"][21] == "CO-21405" and numbers["1.1.2.1.1"][7] == "Highside Supply"
    assert f"{pending['unique_no']}.1" not in numbers
    assert "xl/vbaProject.bin" in zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_reports_download(client, job):
    rec = client.get(f"/api/jobs/{job['id']}/exports").json()[0]
    for kind, title in (("mapping", "Component Mapping"), ("unmatched", "Unmatched")):
        data = client.get(f"/api/exports/{rec['id']}/download/{kind}").content
        assert openpyxl.load_workbook(io.BytesIO(data)).active.title == title


def test_dashboard_counts(client, job):
    d = client.get("/api/dashboard").json()
    assert d["boq_files"] >= 1 and d["components_mapped"] > 0 and d["recent"]
