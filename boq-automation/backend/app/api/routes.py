from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload
from app.core.config import get_settings
from app.core.cost_sheets import REGISTRY
from app.database.session import get_db
from app.models.tables import (
    BOQDescription, BOQUpload, ComponentMapping, ExportRecord, HistoricalBOQ, HistoricalScan, ItemMasterComponent,
    ItemMasterUpload, KnowledgeComponent, KnowledgeParent, MappingApproval, ProcessingHistory, User,
)
from app.schemas import api as s
from app.services import jobs
from app.services.boq_parser.layout import detect_layout, layout_at, validate_layout
from app.services.boq_parser.parser import parse_boq, parse_generic
from app.services.excel_processor.workbook import WorkbookError, inspect_boq, open_values, preview_sheet
from app.services.knowledge import service as knowledge

router = APIRouter(prefix="/api")

def current_user(db: Session = Depends(get_db)) -> User:
    """v1 has no login: every action is recorded against one local user."""
    user = db.scalar(select(User).where(User.name == "local"))
    if user is None:
        user = User(name="local")
        db.add(user)
        db.commit()
    return user


def _audit(db, desc: BOQDescription, action: str, user: User, mapping=None, previous=None, **detail) -> None:
    db.add(MappingApproval(job_id=desc.job_id, description_id=desc.id, mapping_id=mapping.id if mapping else None,
                           action=action, item_no=mapping.item_no if mapping else None, previous_item_no=previous,
                           detail=detail, user_id=user.id))


def _desc(db, did: int) -> BOQDescription:
    d = db.get(BOQDescription, did)
    if d is None:
        raise HTTPException(404, "Description not found")
    return d


def _mapping(db, mid: int) -> ComponentMapping:
    m = db.get(ComponentMapping, mid)
    if m is None:
        raise HTTPException(404, "Mapping not found")
    return m


def _require_eligible(desc: BOQDescription) -> None:
    if desc.status == "incomplete":
        raise HTTPException(409, f"{desc.skip_reason}. Components are only added to BOQ items that have "
                                 "both a unit and a quantity.")


def _settle(desc: BOQDescription) -> None:
    """Keep a description's status honest after manual edits."""
    if desc.status in ("skipped", "mapped", "incomplete"):
        return
    if desc.status == "scope_review" and not desc.mappings:
        return
    if desc.mappings:
        desc.status = "partial" if desc.unmatched else "matched"
    else:
        desc.status = "unmatched"


async def _read_excel(file: UploadFile, allowed: tuple[str, ...]) -> tuple[bytes, str]:
    name = Path(file.filename or "").name
    if not name.lower().endswith(allowed):
        raise HTTPException(400, f"Expected {' or '.join(allowed)}, got '{name}'")
    content = await file.read()
    if not content.startswith(b"PK"):
        raise HTTPException(400, "File is not a valid Excel workbook")
    return content, name


# ---- configuration ----
@router.get("/config/options")
def options():
    cfg = get_settings()
    return {
        "cost_sheet_types": [{"name": n, "group": REGISTRY[n].group, "activity": REGISTRY[n].activity}
                             for n in cfg.enabled_types if n in REGISTRY],
        "all_cost_sheet_types": list(REGISTRY),
        "ai_mode": cfg.resolved_ai_mode, "model": cfg.active_model,
        "embedding_backend": cfg.embedding_backend,
        "database": "postgresql" if cfg.database_url.startswith("postgres") else "sqlite",
        "defaults": {"confidence_threshold": cfg.default_confidence_threshold,
                     "max_candidates": cfg.default_max_candidates},
    }


# ---- uploads ----
@router.post("/uploads/boq", response_model=s.BOQUploadOut)
async def upload_boq(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    content, name = await _read_excel(file, (".xlsx", ".xlsm"))
    path = jobs.store_upload(content, "boq", name)
    try:
        info = inspect_boq(path)
    except WorkbookError as e:
        jobs.delete_upload_folder(path)
        raise HTTPException(422, str(e))
    row = BOQUpload(filename=name, stored_path=str(path), size_bytes=len(content), selected_sheet=info["selected_sheet"],
                    sheets=info["sheets"], has_macros=info["has_macros"], uploaded_by=user.id)
    db.add(row)
    db.commit()
    return row


@router.get("/uploads/boq", response_model=list[s.BOQUploadOut])
def list_boq(db: Session = Depends(get_db)):
    return db.scalars(select(BOQUpload).order_by(BOQUpload.id.desc()).limit(50)).all()


class LayoutIn(BaseModel):
    sheet: str
    header_row: int | None = None
    columns: dict[str, int | None] = {}


def _boq(db, uid: int) -> BOQUpload:
    row = db.get(BOQUpload, uid)
    if row is None:
        raise HTTPException(404, "Upload not found")
    return row


def _layout_summary(parsed) -> dict:
    headings = sum(1 for r in parsed.rows if r.sales_item != "Y")
    sample = [{"row": l.row.row, "text": l.row.text, "context": l.context[:2], "quantity": l.row.billable_qty,
               "unit": l.row.uom} for l in parsed.lines[:6]]
    return {"lines": len(parsed.lines), "headings": headings, "sample": sample, "warnings": parsed.warnings[:5]}


@router.get("/uploads/boq/{uid}/layout")
def detect_boq_layout(uid: int, sheet: str, header_row: int | None = None, db: Session = Depends(get_db)):
    """Suggested columns for a worksheet, optionally from a header row the engineer picked."""
    row = _boq(db, uid)
    wb = open_values(row.stored_path)
    try:
        if sheet not in wb.sheetnames:
            raise HTTPException(404, f"Worksheet not found: {sheet}")
        ws = wb[sheet]
        return layout_at(ws, header_row) if header_row else detect_layout(ws)
    finally:
        wb.close()


@router.put("/uploads/boq/{uid}/layout")
def save_boq_layout(uid: int, body: LayoutIn, db: Session = Depends(get_db)):
    """Confirm which worksheet, header row and columns hold the BOQ. Nothing in the file changes."""
    row = _boq(db, uid)
    wb = open_values(row.stored_path)
    try:
        if body.sheet not in wb.sheetnames:
            raise HTTPException(404, f"Worksheet not found: {body.sheet}")
        ws = wb[body.sheet]
        detected = detect_layout(ws)
        if detected["format"] == "sales_bom" and not body.columns:
            layout = detected
        else:
            layout = layout_at(ws, body.header_row) if body.header_row else detected
            layout.update(columns={k: v for k, v in body.columns.items() if v is not None},
                          confidence="confirmed", needs_mapping=False, notes=[])
            problems = validate_layout(layout)
            if problems:
                raise HTTPException(422, " ".join(problems))
            if layout["format"] == "generic" and layout["header_row"]:
                headers = layout["headers"]
                for role, col in layout["columns"].items():
                    if col >= max(len(headers), ws.max_column or 0):
                        raise HTTPException(422, f"Column {col + 1} for {role} is outside the sheet")
        try:
            parsed = parse_generic(ws, layout) if layout["format"] == "generic" else None
        except WorkbookError as e:
            raise HTTPException(422, str(e))
    finally:
        wb.close()
    if parsed is None:
        parsed = parse_boq(row.stored_path, body.sheet, layout)
    if not parsed.lines:
        raise HTTPException(422, "No BOQ items were found with these columns. " + " ".join(parsed.warnings))
    sheets = [dict(x) for x in row.sheets or []]
    for x in sheets:
        if x.get("name") == body.sheet:
            x["layout"] = layout
            x["header_row"] = layout["header_row"]
    row.sheets = sheets
    row.selected_sheet = body.sheet
    db.commit()
    return {"upload": s.BOQUploadOut.model_validate(row).model_dump(mode="json"), "summary": _layout_summary(parsed)}


@router.get("/uploads/boq/{uid}/preview")
def boq_preview(uid: int, sheet: str | None = None, db: Session = Depends(get_db)):
    row = db.get(BOQUpload, uid)
    if row is None:
        raise HTTPException(404, "Upload not found")
    try:
        return preview_sheet(row.stored_path, sheet or row.selected_sheet)
    except WorkbookError as e:
        raise HTTPException(422, str(e))


@router.post("/uploads/item-master", response_model=s.ItemMasterUploadOut)
async def upload_item_master(tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db),
                             user: User = Depends(current_user)):
    content, name = await _read_excel(file, (".xlsx",))
    path = jobs.store_upload(content, "item_master", name)
    row = ItemMasterUpload(filename=name, stored_path=str(path), size_bytes=len(content), uploaded_by=user.id)
    db.add(row)
    db.commit()
    tasks.add_task(jobs.ingest_item_master, row.id)
    return row


@router.get("/uploads/item-master", response_model=list[s.ItemMasterUploadOut])
def list_item_master(db: Session = Depends(get_db)):
    return db.scalars(select(ItemMasterUpload).order_by(ItemMasterUpload.id.desc()).limit(50)).all()


@router.get("/item-master/{uid}/search")
def search_items(uid: int, q: str = "", cost_sheet_type: str = "", types: str = "", limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db)):
    stmt = select(ItemMasterComponent).where(ItemMasterComponent.upload_id == uid)
    if cost_sheet_type == "(blank)":
        stmt = stmt.where(ItemMasterComponent.cost_sheet_type == "")
    elif cost_sheet_type:
        stmt = stmt.where(ItemMasterComponent.cost_sheet_type == cost_sheet_type)
    elif types:  # filter here, not in the browser, or paging hides in-scope records
        stmt = stmt.where(ItemMasterComponent.cost_sheet_type.in_([t for t in types.split(",") if t]))
    for word in q.split():
        pattern = f"%{word}%"
        stmt = stmt.where(or_(ItemMasterComponent.item_brief.ilike(pattern), ItemMasterComponent.item_no.ilike(pattern)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(ItemMasterComponent.item_brief).limit(limit).offset(offset)).all()
    return {"total": total, "items": [
        {"item_no": r.item_no, "item_brief": r.item_brief, "cost_sheet_type": r.cost_sheet_type, "uom": r.uom,
         "cost": r.cost, "family": r.family} for r in rows]}


@router.get("/item-master/{uid}/items/{item_no}")
def item_detail(uid: int, item_no: str, db: Session = Depends(get_db)):
    row = db.scalar(select(ItemMasterComponent).where(ItemMasterComponent.upload_id == uid,
                                                      ItemMasterComponent.item_no == item_no))
    if row is None:
        raise HTTPException(404, f"{item_no} is not in the Item Master")
    return {"item_no": row.item_no, "raw": {k: v for k, v in row.raw.items() if v != ""}}


# ---- jobs ----
@router.post("/jobs", response_model=s.JobOut)
def create_job(body: s.JobCreate, db: Session = Depends(get_db)):
    cfg = get_settings()
    bad = [t for t in body.cost_sheet_types if t not in cfg.enabled_types or t not in REGISTRY]
    if bad:
        raise HTTPException(400, f"Cost Sheet Type not enabled: {', '.join(bad)}")
    boq = db.get(BOQUpload, body.boq_upload_id)
    im = db.get(ItemMasterUpload, body.item_master_upload_id)
    if boq is None or im is None:
        raise HTTPException(404, "Upload not found")
    if im.status != "ready":
        raise HTTPException(409, f"Item Master is {im.status}")
    layout = jobs.boq_layout(boq, body.sheet or boq.selected_sheet)
    if layout and layout.get("format") == "generic":
        problems = validate_layout(layout)
        if problems:
            raise HTTPException(422, " ".join(problems))
        if layout.get("needs_mapping"):
            raise HTTPException(409, "Confirm the BOQ columns on the Upload page first. "
                                     "A description column is mandatory for AI matching.")
    job = ProcessingHistory(boq_upload_id=boq.id, item_master_upload_id=im.id, sheet=body.sheet or boq.selected_sheet,
                            config=body.model_dump(exclude={"boq_upload_id", "item_master_upload_id", "sheet"}),
                            mode=cfg.resolved_ai_mode)
    db.add(job)
    db.commit()
    jobs.executor.submit(jobs.run_job, job.id)
    return job


@router.get("/jobs", response_model=list[s.JobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.scalars(select(ProcessingHistory).order_by(ProcessingHistory.id.desc()).limit(100)).all()


@router.get("/jobs/{jid}", response_model=s.JobOut)
def get_job(jid: int, db: Session = Depends(get_db)):
    job = db.get(ProcessingHistory, jid)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{jid}/descriptions", response_model=list[s.DescriptionOut])
def descriptions(jid: int, db: Session = Depends(get_db)):
    stmt = (select(BOQDescription).where(BOQDescription.job_id == jid).options(selectinload(BOQDescription.mappings))
            .order_by(BOQDescription.row_number))
    return db.scalars(stmt).all()


@router.get("/jobs/{jid}/audit")
def audit(jid: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(MappingApproval).where(MappingApproval.job_id == jid).order_by(MappingApproval.id.desc())
                      .limit(500)).all()
    return [{"id": r.id, "description_id": r.description_id, "action": r.action, "item_no": r.item_no,
             "previous_item_no": r.previous_item_no, "detail": r.detail, "created_at": r.created_at} for r in rows]


# ---- review ----
@router.post("/mappings/{mid}/approve", response_model=s.MappingOut)
def approve(mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    m.status = "approved"
    _audit(db, m.description, "approve", user, m)
    knowledge.sync_feedback(db, m.description)
    db.commit()
    return m


@router.post("/mappings/{mid}/incorrect", response_model=s.MappingOut)
def incorrect(mid: int, body: s.MarkIncorrect, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    m.status = "incorrect"
    _audit(db, m.description, "mark_incorrect", user, m, note=body.note)
    knowledge.sync_feedback(db, m.description)
    db.commit()
    return m


@router.delete("/mappings/{mid}")
def remove(mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    desc = m.description
    _audit(db, desc, "remove", user, m, item_brief=m.item_brief)
    desc.mappings.remove(m)
    _settle(desc)
    knowledge.sync_feedback(db, desc)
    db.commit()
    return {"ok": True}


def _master(db_job: ProcessingHistory, item_no: str):
    c = jobs.indexes_for(db_job.item_master_upload_id).by_no.get(item_no)
    if c is None:
        raise HTTPException(400, f"{item_no} is not in the Item Master")
    if not c.cost_sheet_type:
        raise HTTPException(400, f"{item_no} has a blank Cost Sheet Type in the Item Master; fix the master first")
    if c.cost_sheet_type not in db_job.config["cost_sheet_types"]:
        raise HTTPException(400, f"{item_no} is {c.cost_sheet_type}, outside this run's selected Cost Sheet Types")
    return c


@router.post("/mappings/{mid}/replace", response_model=s.MappingOut)
def replace(mid: int, body: s.ReplaceMapping, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    desc = m.description
    job = db.get(ProcessingHistory, desc.job_id)
    c = _master(job, body.item_no)
    if any(o.item_no == c.item_no and o.id != m.id for o in desc.mappings):
        raise HTTPException(409, f"{c.item_no} is already mapped to this description")
    previous = m.item_no
    fresh = jobs.mapping_from_master(c, m.confidence, body.note or f"Replaced {previous} manually")
    for field in ("item_no", "item_brief", "cost_sheet_type", "class_name", "cost", "reason", "flags", "source",
                  "status", "master_cost_sheet_type", "ai_cost_sheet_type", "classification_source",
                  "classification_note", "evidence"):
        setattr(m, field, getattr(fresh, field))
    m.scope_label = m.scope_label or fresh.scope_label
    _audit(db, desc, "replace", user, m, previous=previous, note=body.note)
    knowledge.sync_feedback(db, desc)
    db.commit()
    return m


@router.post("/descriptions/{did}/mappings", response_model=s.MappingOut)
def add_mapping(did: int, body: s.AddMapping, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
    _require_eligible(desc)
    job = db.get(ProcessingHistory, desc.job_id)
    c = _master(job, body.item_no)
    if any(m.item_no == c.item_no for m in desc.mappings) or \
            any(e["item_no"] == c.item_no for e in desc.existing_children):
        raise HTTPException(409, f"{c.item_no} is already mapped to this description")
    m = jobs.mapping_from_master(c, 100, "Added manually by an engineer")
    desc.mappings.append(m)
    _settle(desc)
    db.flush()
    _audit(db, desc, "add", user, m)
    knowledge.sync_feedback(db, desc)
    db.commit()
    return m


@router.post("/descriptions/{did}/approve-all", response_model=s.DescriptionOut)
def approve_all(did: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Approves 'pending' only: 'review' items must each be looked at individually."""
    desc = _desc(db, did)
    for m in desc.mappings:
        if m.status == "pending":
            m.status = "approved"
            _audit(db, desc, "approve", user, m, bulk=True)
    knowledge.sync_feedback(db, desc)
    db.commit()
    return desc


@router.post("/mappings/{mid}/cost-sheet-type", response_model=s.MappingOut)
def reclassify(mid: int, body: s.Reclassify, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Correct the Cost Sheet Type of one suggestion. The correction is approved, exported, and
    remembered as feedback for future runs; the Item Master's own value is kept alongside."""
    m = _mapping(db, mid)
    job = db.get(ProcessingHistory, m.description.job_id)
    if body.cost_sheet_type not in REGISTRY or body.cost_sheet_type not in job.config["cost_sheet_types"]:
        raise HTTPException(400, f"'{body.cost_sheet_type}' is not one of this run's Cost Sheet Types. "
                                 "Remove the component if it belongs to another scope.")
    previous = m.cost_sheet_type
    if previous != body.cost_sheet_type:
        m.cost_sheet_type = body.cost_sheet_type
        m.classification_source = "manual"
        m.classification_note = (f"Corrected from {previous} by an engineer" + (f": {body.note}" if body.note else ""))
    m.status = "approved"
    _audit(db, m.description, "reclassify", user, m, before=previous, after=body.cost_sheet_type, note=body.note)
    knowledge.sync_feedback(db, m.description)
    db.commit()
    return m


@router.post("/descriptions/{did}/regenerate", response_model=s.DescriptionOut)
def regenerate(did: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
    _require_eligible(desc)
    try:
        jobs.regenerate(db, desc, user.id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return desc


@router.post("/descriptions/{did}/flag", response_model=s.DescriptionOut)
def flag(did: int, body: s.FlagRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
    desc.flagged, desc.flag_note = body.flagged, body.note
    _audit(db, desc, "flag" if body.flagged else "unflag", user, note=body.note)
    db.commit()
    return desc


@router.patch("/descriptions/{did}/requirements", response_model=s.DescriptionOut)
def edit_requirements(did: int, body: s.RequirementsEdit, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
    before = dict(desc.requirements)
    desc.requirements = {**before, **body.model_dump()}
    desc.requirements_edited = True
    _audit(db, desc, "edit_requirements", user, before={k: before.get(k) for k in body.model_dump()},
           after=body.model_dump())
    db.commit()
    return desc


# ---- export ----
@router.post("/jobs/{jid}/export", response_model=s.ExportOut)
def export(jid: int, db: Session = Depends(get_db)):
    job = db.get(ProcessingHistory, jid)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "completed":
        raise HTTPException(409, f"Job is {job.status}")
    return jobs.run_export(db, job)


@router.get("/jobs/{jid}/exports", response_model=list[s.ExportOut])
def job_exports(jid: int, db: Session = Depends(get_db)):
    return db.scalars(select(ExportRecord).where(ExportRecord.job_id == jid).order_by(ExportRecord.id.desc())).all()


@router.get("/exports", response_model=list[s.ExportOut])
def all_exports(db: Session = Depends(get_db)):
    return db.scalars(select(ExportRecord).order_by(ExportRecord.id.desc()).limit(100)).all()


@router.get("/exports/{eid}/download/{kind}")
def download(eid: int, kind: str, db: Session = Depends(get_db)):
    rec = db.get(ExportRecord, eid)
    if rec is None:
        raise HTTPException(404, "Export not found")
    path = {"boq": rec.boq_path, "mapping": rec.mapping_report_path, "unmatched": rec.unmatched_report_path}.get(kind)
    if not path:
        raise HTTPException(404, "This export has no such file; verification may have failed")
    return FileResponse(path, filename=Path(path).name)


# ---- workflow progress (sidebar) ----
@router.get("/progress")
def progress(boq: int | None = None, master: int | None = None, job: int | None = None, db: Session = Depends(get_db)):
    """Where the user is in the workflow, in one small request instead of five large ones."""
    run = db.get(ProcessingHistory, job) if job else None
    approved = bool(job and db.scalar(
        select(func.count(ComponentMapping.id)).join(BOQDescription, BOQDescription.id == ComponentMapping.description_id)
        .where(BOQDescription.job_id == job, ComponentMapping.status == "approved")))
    im = db.get(ItemMasterUpload, master) if master else None
    return {"boqReady": bool(boq and db.get(BOQUpload, boq)), "masterReady": bool(im and im.status == "ready"),
            "runStatus": run.status if run else None, "hasApprovals": approved,
            "exported": bool(job and db.scalar(select(func.count(ExportRecord.id)).where(
                ExportRecord.job_id == job, ExportRecord.status == "verified")))}


# ---- dashboard ----
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    threshold = get_settings().default_confidence_threshold
    m = db.execute(select(
        func.count(ComponentMapping.id),
        func.sum(case((ComponentMapping.status == "approved", 1), else_=0)),
        func.sum(case((ComponentMapping.status.in_(("pending", "review")), 1), else_=0)),
        func.sum(case((ComponentMapping.confidence < threshold, 1), else_=0)),
    )).one()
    recent = db.scalars(select(ProcessingHistory).options(selectinload(ProcessingHistory.boq))
                        .order_by(ProcessingHistory.id.desc()).limit(8)).all()
    return {
        "knowledge": knowledge.stats(db),
        "boq_files": db.scalar(select(func.count(BOQUpload.id))) or 0,
        "parent_descriptions": db.scalar(select(func.count(BOQDescription.id)).where(
            BOQDescription.status.notin_(("skipped", "mapped", "incomplete")))) or 0,
        "skipped_unit_qty": db.scalar(select(func.count(BOQDescription.id)).where(
            BOQDescription.status == "incomplete")) or 0,
        "components_mapped": m[0] or 0, "approved": m[1] or 0, "pending": m[2] or 0, "low_confidence": m[3] or 0,
        "recent": [{"id": j.id, "filename": j.boq.filename, "status": j.status, "mode": j.mode, "total": j.total,
                    "processed": j.processed, "created_at": j.created_at} for j in recent],
    }



# ---- historical BOQs and the knowledge base ----
def _historical(db, hid: int) -> HistoricalBOQ:
    h = db.get(HistoricalBOQ, hid)
    if h is None:
        raise HTTPException(404, "Historical BOQ not found")
    return h


@router.post("/historical", response_model=s.HistoricalOut)
async def upload_historical(file: UploadFile = File(...), db: Session = Depends(get_db),
                            user: User = Depends(current_user)):
    """Add a completed, verified BOQ to the knowledge base. It is only read, never mapped.
    Processing starts automatically in the background."""
    content, name = await _read_excel(file, (".xlsx", ".xlsm"))
    digest = knowledge.sha256(content)
    twin = db.scalar(select(HistoricalBOQ).where(
        HistoricalBOQ.sha256 == digest, HistoricalBOQ.status.in_(knowledge.STORED + knowledge.BUSY + ("no_mappings",))))
    if twin is not None:
        raise HTTPException(409, f"This file is already in the knowledge base as '{twin.filename}' (#{twin.id}).")
    path = knowledge.store_copy(content, name)
    row = HistoricalBOQ(filename=name, stored_path=str(path), size_bytes=len(content), sha256=digest,
                        uploaded_by=user.id, origin="upload")
    db.add(row)
    db.commit()
    knowledge.submit(row.id)
    return row


@router.get("/historical", response_model=list[s.HistoricalOut])
def list_historical(q: str = "", status: str = "", scan_id: int | None = None, limit: int = Query(1000, le=5000),
                    db: Session = Depends(get_db)):
    stmt = select(HistoricalBOQ).order_by(HistoricalBOQ.id.desc())
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(HistoricalBOQ.filename.ilike(pattern), HistoricalBOQ.relative_path.ilike(pattern)))
    if status == "pending":
        stmt = stmt.where(HistoricalBOQ.status.in_(knowledge.BUSY))
    elif status == "stored":
        stmt = stmt.where(HistoricalBOQ.status.in_(knowledge.STORED))
    elif status:
        stmt = stmt.where(HistoricalBOQ.status == status)
    if scan_id is not None:
        stmt = stmt.where(HistoricalBOQ.scan_id == scan_id)
    return db.scalars(stmt.limit(limit)).all()


# ---- folder ingestion (declared before /historical/{hid} so "scans" is not read as an id) ----
@router.post("/historical/scans", response_model=s.ScanOut)
def start_scan(body: s.ScanIn, db: Session = Depends(get_db)):
    """Scan a folder (and every subfolder) on the server for historical BOQs. New files are
    ingested, unchanged files are skipped, changed files are reprocessed (or flagged when
    reprocess_changed is false). The folder is only read."""
    running = db.scalar(select(HistoricalScan).where(HistoricalScan.status.in_(("scanning", "processing"))))
    if running is not None:
        raise HTTPException(409, f"A folder scan is already running (#{running.id}); wait for it to finish")
    try:
        return knowledge.start_scan(db, body.root_path, body.reprocess_changed)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/historical/scans", response_model=list[s.ScanOut])
def list_scans(db: Session = Depends(get_db)):
    return db.scalars(select(HistoricalScan).order_by(HistoricalScan.id.desc()).limit(50)).all()


@router.get("/historical/scans/{sid}")
def get_scan(sid: int, status: str = "", db: Session = Depends(get_db)):
    """A scan with its complete file inventory (filename, folder, format, status, reason)."""
    scan = db.get(HistoricalScan, sid)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    inv = scan.inventory or []
    ids = [e["historical_id"] for e in inv if e.get("historical_id")]
    live = {h.id: (h.status, h.error) for h in db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.id.in_(ids or [-1])))}
    out = []
    for e in inv:
        e = dict(e)
        if e.get("historical_id") in live:
            e["status"], e["reason"] = live[e["historical_id"]][0], live[e["historical_id"]][1] or e.get("reason", "")
        if not status or e["status"] == status:
            out.append(e)
    return {**s.ScanOut.model_validate(scan).model_dump(mode="json"), "inventory": out}


@router.post("/historical/retry-failed")
def retry_failed(db: Session = Depends(get_db)):
    """Queue every failed file again (re-reading folder files from their original location)."""
    return {"queued": len(knowledge.retry_failed(db))}


@router.post("/historical/reprocess-all")
def reprocess_all(db: Session = Depends(get_db)):
    """Re-extract every historical BOQ, for example after the extraction logic improved."""
    rows = db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.status.notin_(knowledge.BUSY + ("duplicate",)),
                                                  HistoricalBOQ.stored_path != "")).all()
    for h in rows:
        h.status, h.error = "queued", None
    db.commit()
    for h in rows:
        knowledge.submit(h.id)
    return {"queued": len(rows)}


@router.get("/historical/{hid}", response_model=s.HistoricalOut)
def get_historical(hid: int, db: Session = Depends(get_db)):
    return _historical(db, hid)


@router.get("/historical/{hid}/mappings")
def historical_mappings(hid: int, q: str = "", scope: str = "highside", limit: int = Query(50, le=500), offset: int = 0,
                        db: Session = Depends(get_db)):
    """The parent descriptions and components extracted from one historical BOQ.
    scope=highside (default) lists Highside mappings only; scope=all includes the
    out-of-scope ones kept for classification checks."""
    _historical(db, hid)
    stmt = select(KnowledgeParent).where(KnowledgeParent.historical_id == hid)
    if scope != "all":
        stmt = stmt.where(KnowledgeParent.in_scope.is_(True))
    if q.strip():
        stmt = stmt.where(KnowledgeParent.description.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(KnowledgeParent.components)).order_by(KnowledgeParent.id)
                      .limit(limit).offset(offset)).all()
    return {"total": total, "items": [s.KnowledgeParentOut.model_validate(r).model_dump(mode="json") for r in rows]}


@router.post("/historical/{hid}/reprocess", response_model=s.HistoricalOut)
def reprocess_historical(hid: int, db: Session = Depends(get_db)):
    """Extract one file again (also the way to apply a change a folder scan detected). Its
    previous mappings are replaced, never duplicated."""
    h = _historical(db, hid)
    if h.status in knowledge.BUSY:
        raise HTTPException(409, "This file is already being processed")
    if h.status == "duplicate" and h.duplicate_of:
        raise HTTPException(409, f"This file is a copy of #{h.duplicate_of}; reprocess that one instead")
    if not h.stored_path:
        raise HTTPException(409, "This file could not be read when it was found; use Retry failed instead")
    h.status, h.error = "queued", None
    db.commit()
    knowledge.submit(h.id)
    return h


@router.put("/historical/{hid}/file", response_model=s.HistoricalOut)
async def replace_historical(hid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Replace a historical BOQ with a corrected version; its old knowledge is removed first."""
    h = _historical(db, hid)
    if h.status in knowledge.BUSY:
        raise HTTPException(409, "This file is being processed; try again when it finishes")
    content, name = await _read_excel(file, (".xlsx", ".xlsm"))
    old = Path(h.stored_path) if h.stored_path else None
    path = knowledge.store_copy(content, name)
    h.filename, h.stored_path, h.size_bytes, h.sha256 = name, str(path), len(content), knowledge.sha256(content)
    h.status, h.error, h.fingerprint, h.duplicate_of = "queued", None, "", None
    db.commit()
    if old is not None and knowledge._owned(old) and not knowledge._shared(db, old, h.id):
        jobs.delete_upload_folder(old)
    knowledge.submit(h.id)
    return h


@router.delete("/historical/{hid}")
def delete_historical(hid: int, db: Session = Depends(get_db)):
    """Remove a file and the knowledge learned from it. An original in a scanned folder is
    never touched; only the application's own copy is deleted."""
    h = _historical(db, hid)
    if h.status in knowledge.BUSY:
        raise HTTPException(409, "This file is being processed; try again when it finishes")
    path = Path(h.stored_path) if h.stored_path else None
    shared = path is not None and knowledge._shared(db, path, h.id)
    knowledge.delete_historical(db, h)
    if path is not None and knowledge._owned(path) and not shared:
        jobs.delete_upload_folder(path)
    return {"ok": True}


# ---- knowledge base ----
@router.get("/knowledge/stats")
def knowledge_stats(db: Session = Depends(get_db)):
    return knowledge.stats(db)


@router.post("/knowledge/index/rebuild")
def rebuild_index():
    """Build the vector index now (it is otherwise rebuilt on the next mapping run)."""
    return knowledge.warm_index()


@router.get("/knowledge/mappings")
def knowledge_mappings(q: str = "", item_no: str = "", cost_sheet_type: str = "", folder: str = "",
                       limit: int = Query(25, le=200), offset: int = 0, db: Session = Depends(get_db)):
    """Search every stored historical mapping by description words, Item Number, Cost Sheet
    Type or project folder. Each result names its source file, worksheet and rows."""
    stmt = (select(KnowledgeParent).join(HistoricalBOQ, HistoricalBOQ.id == KnowledgeParent.historical_id)
            .where(KnowledgeParent.source == "historical", KnowledgeParent.in_scope.is_(True),
                   HistoricalBOQ.status.in_(knowledge.STORED)))
    for word in q.split():
        stmt = stmt.where(KnowledgeParent.description.ilike(f"%{word}%"))
    if item_no.strip() or cost_sheet_type:
        sub = select(KnowledgeComponent.parent_id).where(KnowledgeComponent.in_scope.is_(True))
        if item_no.strip():
            sub = sub.where(KnowledgeComponent.item_no.ilike(f"%{item_no.strip()}%"))
        if cost_sheet_type:
            sub = sub.where(KnowledgeComponent.resolved_cost_sheet_type == cost_sheet_type)
        stmt = stmt.where(KnowledgeParent.id.in_(sub))
    if folder.strip():
        stmt = stmt.where(HistoricalBOQ.relative_path.ilike(f"%{folder.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(KnowledgeParent.components)).order_by(KnowledgeParent.id.desc())
                      .limit(limit).offset(offset)).all()
    files = {h.id: h for h in db.scalars(select(HistoricalBOQ).where(
        HistoricalBOQ.id.in_({r.historical_id for r in rows} or {-1})))}
    items = []
    for r in rows:
        h = files.get(r.historical_id)
        items.append({**s.KnowledgeParentOut.model_validate(r).model_dump(mode="json"),
                      "source": {"historical_id": r.historical_id, "filename": h.filename if h else r.filename,
                                 "relative_path": h.relative_path if h else "", "source_path": h.source_path if h else "",
                                 "folder": h.folder if h else "", "status": h.status if h else ""}})
    return {"total": total, "items": items}


@router.get("/knowledge/parents/{pid}")
def knowledge_parent(pid: int, db: Session = Depends(get_db)):
    """One stored mapping with full source traceability."""
    p = db.get(KnowledgeParent, pid)
    if p is None:
        raise HTTPException(404, "Mapping not found")
    h = db.get(HistoricalBOQ, p.historical_id) if p.historical_id else None
    return {**s.KnowledgeParentOut.model_validate(p).model_dump(mode="json"), "attrs": p.attrs,
            "source": None if h is None else {
                "historical_id": h.id, "filename": h.filename, "source_path": h.source_path,
                "relative_path": h.relative_path, "folder": h.folder, "status": h.status, "sha256": h.sha256,
                "extracted_at": (h.summary or {}).get("extracted_at"), "processed_at": h.processed_at}}


@router.get("/knowledge/search")
def knowledge_search(q: str = Query(..., min_length=2), limit: int = Query(10, le=50)):
    """What the knowledge base would offer for a description: similar verified examples whose
    technical attributes are compatible with it."""
    from app.services.ai_matching.requirements import build_requirement
    from app.services.boq_parser.parser import BoqRow, SalesLine

    kb = knowledge.snapshot()
    if kb.empty:
        return {"items": []}
    req = build_requirement(SalesLine(BoqRow(0, "", "", "", "", q, "Y", "", 0, "", 0, "", "")))
    known = {c.item_no for p in kb.parents for c in p.components}
    ev = kb.evidence(q, [], req.attrs, known, get_settings().enabled_types)
    return {"items": ev.payload(limit)}
