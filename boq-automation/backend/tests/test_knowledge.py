"""Historical BOQ knowledge base: extraction, priority over the Item Master, technical
compatibility, conflicts, duplicates, feedback learning and Lowside detection."""
import time

import openpyxl
import pytest

from app.services.ai_matching.engine import SCOPE_REVIEW, MatchConfig, MatchingEngine
from app.services.ai_matching.retrieval import MasterIndexes
from app.services.knowledge.extract import extract_workbook
from app.services.knowledge.index import KnowledgeSnapshot, KnownComponent, KnownParent, compatibility, parent_attrs
from app.services.item_master.normalize import extract_attrs

TYPES = ["Highside Supply", "Highside Installation"]
ODU = "CO-21405"  # DAIKIN RXQ38BRY16 ODU UNIT, Highside Supply in the master
ODU_INSTALL = "CO-16001"  # INSTALLATION OF ODU (SIDE DISCHARGE): Lowside Installation in the master


def known(pid, desc, comps, source="historical", context=()):
    return KnownParent(pid, source, f"past{pid}.xlsx", "BOQ", 10 + pid, desc, list(context),
                       [KnownComponent(no, brief, t, "Component") for no, brief, t in comps],
                       parent_attrs(desc, list(context)))


def engine_with(components, parents):
    kb = KnowledgeSnapshot(parents)
    overrides = kb.classifier.overrides({c.item_no: c.cost_sheet_type for c in components})
    return MatchingEngine(MasterIndexes(components, overrides), MatchConfig(TYPES, knowledge=kb))


def historical_boq(path, items, header=("Sl No", "Description", "Unit", "Qty", "Item Code", "Cost Sheet Type"),
                   title="Completed BOQ"):
    """A completed client-format BOQ: BOQ items with their component rows (Item Code) below."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mapped BOQ"
    ws["A1"] = title
    for c, h in enumerate(header, start=1):
        ws.cell(2, c, h)
    r = 3
    for serial, desc, unit, qty, comps in items:
        for c, v in enumerate((serial, desc, unit, qty), start=1):
            ws.cell(r, c, v)
        r += 1
        for no, brief, cst in comps:
            ws.cell(r, 2, brief)
            ws.cell(r, 5, no)
            ws.cell(r, 6, cst)
            r += 1
    wb.save(path)
    return path


ODU_LINE = "Supply, installation, testing and commissioning of VRV outdoor unit 38 HP RXQ38BRY16"


def odu_items(cst=("Highside Supply", "Highside Installation")):
    return [("1", ODU_LINE, "Nos", 1, [(ODU, "DAIKIN RXQ38BRY16 ODU UNIT", cst[0]),
                                       (ODU_INSTALL, "INSTALLATION OF ODU (SIDE DISCHARGE)", cst[1])])]


# ---------- extraction ----------

def test_sales_bom_mappings_are_extracted_with_their_source(sample_boq, components):
    found = extract_workbook(sample_boq, {c.item_no for c in components})
    assert found.parents, found.sheets
    p = found.parents[0]
    assert p.sheet == "Sales BOM" and p.row > 2 and p.components
    assert all(c.item_no and c.cost_sheet_type for p in found.parents for c in p.components)
    assert [c.item_no for p in found.parents for c in p.components] == ["CO-01185"]  # the one mapped line


def test_client_format_components_found_by_their_values(tmp_path, components):
    path = historical_boq(tmp_path / "h.xlsx", odu_items())
    found = extract_workbook(path, {c.item_no for c in components})
    assert len(found.parents) == 1
    p = found.parents[0]
    assert p.description == ODU_LINE
    assert [(c.item_no, c.cost_sheet_type) for c in p.components] == [
        (ODU, "Highside Supply"), (ODU_INSTALL, "Highside Installation")]


def test_workbook_without_mappings_yields_nothing(tmp_path, components):
    from tests.test_generic_boq import client_boq
    found = extract_workbook(client_boq(tmp_path / "plain.xlsx"), {c.item_no for c in components})
    assert found.parents == []


# ---------- priority and compatibility ----------

def test_verified_history_overrides_the_item_master_classification(components):
    parents = [known(i, ODU_LINE, [(ODU, "ODU", "Highside Supply"),
                                   (ODU_INSTALL, "INSTALLATION OF ODU", "Highside Installation")]) for i in (1, 2)]
    eng = engine_with(components, parents)
    assert eng.indexes.by_no[ODU_INSTALL].cost_sheet_type == "Highside Installation"
    assert eng.indexes.by_no[ODU_INSTALL].original_cost_sheet_type == "Lowside Installation"

    from app.services.boq_parser.parser import BoqRow, SalesLine
    line = SalesLine(BoqRow(5, "1", "", "", "", ODU_LINE, "Y", "", 1, "Nos", 0, "", ""))
    r = eng.match_line(line)
    got = {s.component.item_no: s for s in r.selections}
    assert ODU in got and ODU_INSTALL in got, (r.unmatched, r.notes)
    inst = got[ODU_INSTALL]
    assert inst.component.cost_sheet_type == "Highside Installation"
    assert inst.classification.source == "historical"
    assert inst.component.original_cost_sheet_type == "Lowside Installation"
    assert inst.evidence and inst.evidence[0]["file"].startswith("past")


def test_without_history_the_master_classification_stands(components):
    eng = MatchingEngine(MasterIndexes(components), MatchConfig(TYPES))
    assert eng.indexes.by_no[ODU_INSTALL].cost_sheet_type == "Lowside Installation"


def test_history_for_a_different_model_or_capacity_is_not_evidence():
    line = extract_attrs("VRV OUTDOOR UNIT 38 HP RXQ38BRY16")
    assert compatibility(line, extract_attrs("VRV OUTDOOR UNIT 38 HP RXQ38BRY16"))[0]
    assert not compatibility(line, extract_attrs("VRV OUTDOOR UNIT 22 HP RXQ22BRY16"))[0]
    assert not compatibility(extract_attrs("COPPER PIPE 12.7 MM"), extract_attrs("COPPER PIPE 22.2 MM"))[0]
    assert not compatibility(extract_attrs("4 WAY CASSETTE 2 TR"), extract_attrs("1 WAY CASSETTE 2 TR"))[0]


def test_similar_wording_with_different_specification_is_ignored(components):
    other = "Supply, installation, testing and commissioning of VRV outdoor unit 22 HP RXQ22BRY16"
    kb = KnowledgeSnapshot([known(1, other, [(ODU_INSTALL, "INSTALLATION OF ODU", "Highside Installation")])])
    ev = kb.evidence(ODU_LINE, [], parent_attrs(ODU_LINE, []), {c.item_no for c in components}, TYPES)
    assert ODU_INSTALL not in ev.items


def test_consistent_history_is_applied_and_conflicts_are_flagged():
    from collections import Counter
    from app.services.knowledge.index import Classifier

    agree = Classifier({"X": Counter({"Highside Supply": 4})}, {})
    c = agree.resolve("X", "Lowside Supply")
    assert (c.cost_sheet_type, c.source, c.conflict) == ("Highside Supply", "historical", False)

    split = Classifier({"X": Counter({"Highside Supply": 2, "Lowside Supply": 2})}, {})
    c = split.resolve("X", "Lowside Supply")
    assert c.conflict and c.cost_sheet_type == "Lowside Supply" and "disagree" in c.note

    # Feedback counts only when no verified BOQ has used the item.
    fb = Classifier({}, {"X": Counter({"Highside Installation": 3})})
    assert fb.resolve("X", "Lowside Installation").source == "feedback"
    assert Classifier({}, {}).resolve("X", "Lowside Supply").source == "item_master"


def test_close_technical_context_resolves_a_conflict(components):
    """Verified BOQs disagree overall, but the ones like this line agree: use theirs."""
    near = [known(i, ODU_LINE, [(ODU_INSTALL, "INSTALLATION OF ODU", "Highside Installation")]) for i in (1, 2, 3)]
    far = [known(i, "Installation of ventilation axial fan 6000 CFM", [(ODU_INSTALL, "x", "Lowside Installation")])
           for i in (4, 5, 6)]
    kb = KnowledgeSnapshot(near + far)
    ev = kb.evidence(ODU_LINE, [], parent_attrs(ODU_LINE, []), {c.item_no for c in components}, TYPES)
    c = kb.classifier.resolve(ODU_INSTALL, "Lowside Installation", ev.items[ODU_INSTALL])
    assert c.cost_sheet_type == "Highside Installation" and not c.conflict


def test_unknown_item_numbers_from_history_are_never_used(components):
    kb = KnowledgeSnapshot([known(1, ODU_LINE, [("CO-99999999", "INVENTED", "Highside Supply")])])
    ev = kb.evidence(ODU_LINE, [], parent_attrs(ODU_LINE, []), {c.item_no for c in components}, TYPES)
    assert "CO-99999999" not in ev.items


def test_no_suitable_component_is_reported_not_forced(engine):
    from app.services.boq_parser.parser import BoqRow, SalesLine
    r = engine.match_line(SalesLine(BoqRow(5, "1", "", "", "", "Supply of FXZZ999ZZZ99 special unit", "Y",
                                           "Highside Supply", 1, "Nos", 0, "", "")))
    assert not any(s.component.item_no.startswith("CO-99") for s in r.selections)
    assert r.unmatched or all(s.flags for s in r.selections)


# ---------- Lowside and unsupported scope ----------

def test_lowside_description_is_flagged_not_mapped(engine):
    from app.services.boq_parser.parser import BoqRow, SalesLine
    r = engine.match_line(SalesLine(BoqRow(5, "1", "", "", "", "Supply and installation of GSS ducting 24 G",
                                           "Y", "", 120, "Sqm", 0, "", "")))
    assert r.status == SCOPE_REVIEW and not r.selections and "Lowside" in r.scope_note


def test_history_mapped_to_lowside_flags_the_line(components):
    duct = "Supply and installation of volume control dampers"
    kb = [known(i, duct, [("CO-17640", "VOLUME CONTROL DAMPERS", "Lowside Installation")]) for i in (1, 2)]
    eng = engine_with(components, kb)
    from app.services.boq_parser.parser import BoqRow, SalesLine
    r = eng.match_line(SalesLine(BoqRow(5, "1", "", "", "", duct, "Y", "", 10, "Nos", 0, "", "")))
    assert r.status == SCOPE_REVIEW and not r.selections


# ---------- API: upload, duplicates, reprocess, delete, learning from review ----------

def wait_for(client, hid, statuses=("ingested", "partial", "no_mappings", "failed", "duplicate")):
    for _ in range(200):
        h = client.get(f"/api/historical/{hid}").json()
        if h["status"] in statuses:
            return h
        time.sleep(0.1)
    raise AssertionError("historical BOQ did not finish processing")


@pytest.fixture(scope="module")
def api(sample_master):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_api_flow import wait

    with TestClient(app) as c:
        m = c.post("/api/uploads/item-master", files={"file": ("m.xlsx", sample_master.read_bytes())}).json()
        wait(lambda: next(x for x in c.get("/api/uploads/item-master").json() if x["id"] == m["id"]),
             lambda r: r["status"] == "ready")
        yield c, m["id"]


def test_historical_upload_is_indexed_and_searchable(api, tmp_path):
    c, _ = api
    path = historical_boq(tmp_path / "past.xlsx", odu_items())
    h = c.post("/api/historical", files={"file": ("past.xlsx", path.read_bytes())})
    assert h.status_code == 200, h.text
    h = wait_for(c, h.json()["id"])
    assert h["status"] == "ingested" and h["parents_count"] == 1 and h["components_count"] == 2
    assert h["summary"]["classification_differs_from_master"] == 1  # CO-16001 Lowside -> Highside
    rows = c.get(f"/api/historical/{h['id']}/mappings").json()
    assert rows["total"] == 1 and rows["items"][0]["components"][1]["master_cost_sheet_type"] == "Lowside Installation"
    found = c.get("/api/knowledge/search", params={"q": ODU_LINE}).json()["items"]
    assert found and found[0]["file"] == "past.xlsx"
    stats = c.get("/api/knowledge/stats").json()
    assert stats["files"]["indexed"] >= 1 and stats["historical_parents"] >= 1


def test_duplicate_uploads_are_refused_or_marked(api, tmp_path):
    c, _ = api
    items = [("1", "Supply of VRV indoor cassette unit FXKQ63ARV16", "Nos", 2,
              [("CO-01383", "DAIKIN FXKQ63ARV16 IDU UNIT", "Highside Supply")])]
    path = historical_boq(tmp_path / "a.xlsx", items, title="Project A")
    first = wait_for(c, c.post("/api/historical", files={"file": ("a.xlsx", path.read_bytes())}).json()["id"])
    again = c.post("/api/historical", files={"file": ("a-copy.xlsx", path.read_bytes())})
    assert again.status_code == 409
    # The same mappings saved again (different bytes) are recognised by their content.
    wb = openpyxl.load_workbook(path)
    wb.active["H1"] = "re-saved"
    resaved = tmp_path / "b.xlsx"
    wb.save(resaved)
    twin = wait_for(c, c.post("/api/historical", files={"file": ("b.xlsx", resaved.read_bytes())}).json()["id"])
    assert twin["status"] in ("duplicate", "ingested")
    if first["status"] == "ingested":
        assert twin["status"] == "duplicate" and "already in the knowledge base" in twin["error"]
    assert c.delete(f"/api/historical/{twin['id']}").status_code == 200


def test_failed_reprocess_and_delete(api, tmp_path):
    from tests.test_generic_boq import client_boq
    c, _ = api
    path = client_boq(tmp_path / "plain.xlsx")
    h = wait_for(c, c.post("/api/historical", files={"file": ("plain.xlsx", path.read_bytes())}).json()["id"])
    assert h["status"] == "no_mappings" and "Item Master numbers" in h["error"]
    assert c.post(f"/api/historical/{h['id']}/reprocess").status_code == 200
    assert wait_for(c, h["id"])["status"] == "no_mappings"
    assert c.delete(f"/api/historical/{h['id']}").status_code == 200
    assert c.get(f"/api/historical/{h['id']}").status_code == 404


def test_new_boq_uses_history_and_review_corrections_are_learned(api, tmp_path):
    """End to end: a verified BOQ reclassifies CO-16001; a new BOQ gets it as Highside
    Installation with traceability; a reviewer's correction becomes feedback; export writes it."""
    from tests.test_api_flow import wait
    c, master_id = api
    hist = historical_boq(tmp_path / "verified.xlsx", odu_items(), title="Verified project")
    wait_for(c, c.post("/api/historical", files={"file": ("verified.xlsx", hist.read_bytes())}).json()["id"],
             ("ingested", "partial", "duplicate", "failed"))

    wb = openpyxl.Workbook()
    ws = wb.active
    for col, h in enumerate(["S.No", "Description", "Unit", "Qty"], start=1):
        ws.cell(1, col, h)
    for col, v in enumerate(["1", ODU_LINE, "Nos", 2], start=1):
        ws.cell(2, col, v)
    for col, v in enumerate(["2", "Supply of VRV indoor cassette unit FXKQ63ARV16", "Nos", 4], start=1):
        ws.cell(3, col, v)
    new = tmp_path / "new.xlsx"
    wb.save(new)
    b = c.post("/api/uploads/boq", files={"file": ("new.xlsx", new.read_bytes())}).json()
    j = c.post("/api/jobs", json={"boq_upload_id": b["id"], "item_master_upload_id": master_id,
                                  "cost_sheet_types": TYPES}).json()
    j = wait(lambda: c.get(f"/api/jobs/{j['id']}").json(), lambda r: r["status"] in ("completed", "failed"))
    assert j["status"] == "completed", j["error"]
    assert j["summary"]["knowledge"]["historical_descriptions"] >= 1
    descs = {d["row_number"]: d for d in c.get(f"/api/jobs/{j['id']}/descriptions").json()}
    odu = {m["item_no"]: m for m in descs[2]["mappings"]}
    assert ODU_INSTALL in odu, descs[2]
    inst = odu[ODU_INSTALL]
    assert inst["cost_sheet_type"] == "Highside Installation" and inst["master_cost_sheet_type"] == "Lowside Installation"
    assert inst["classification_source"] == "historical" and inst["evidence"]
    assert descs[2]["requirements"]["historical_examples"]

    # A reviewer corrects the indoor unit's type and approves: that becomes feedback knowledge.
    idu = descs[3]["mappings"][0]
    fixed = c.post(f"/api/mappings/{idu['id']}/cost-sheet-type", json={"cost_sheet_type": "Highside Supply",
                                                                       "note": "checked"}).json()
    assert fixed["status"] == "approved"
    assert c.post(f"/api/mappings/{idu['id']}/cost-sheet-type",
                  json={"cost_sheet_type": "Lowside Supply"}).status_code == 400
    for m in odu.values():
        c.post(f"/api/mappings/{m['id']}/approve")
    stats = c.get("/api/knowledge/stats").json()
    assert stats["feedback_parents"] >= 2

    rec = c.post(f"/api/jobs/{j['id']}/export").json()
    assert rec["status"] == "verified", rec["verification"]
    out = tmp_path / "out.xlsx"
    out.write_bytes(c.get(f"/api/exports/{rec['id']}/download/boq").content)
    ws = openpyxl.load_workbook(out).active
    written = {ws.cell(r, 5).value: ws.cell(r, 6).value for r in range(2, ws.max_row + 1) if ws.cell(r, 5).value}
    assert written[ODU_INSTALL] == "Highside Installation"

    # Removing the approval removes the feedback for that line.
    before = c.get("/api/knowledge/stats").json()["feedback_parents"]
    c.delete(f"/api/mappings/{idu['id']}")
    assert c.get("/api/knowledge/stats").json()["feedback_parents"] == before - 1
