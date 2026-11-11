from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.cost_sheets import REGISTRY
from app.database.session import get_db
from app.models.tables import (
    BOQDescription, BOQUpload, ComponentMapping, ExportRecord, ItemMasterComponent, ItemMasterUpload,
    MappingApproval, ProcessingHistory, User,
)
from app.schemas import api as s
from app.services import jobs
from app.services.excel_processor.workbook import WorkbookError, inspect_boq, preview_sheet
from app.services.item_master.processor import build_component

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


def _settle(desc: BOQDescription) -> None:
    """Keep a description's status honest after manual edits."""
    if desc.status in ("skipped", "mapped"):
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
    db.commit()
    return m


@router.post("/mappings/{mid}/incorrect", response_model=s.MappingOut)
def incorrect(mid: int, body: s.MarkIncorrect, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    m.status = "incorrect"
    _audit(db, m.description, "mark_incorrect", user, m, note=body.note)
    db.commit()
    return m


@router.delete("/mappings/{mid}")
def remove(mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = _mapping(db, mid)
    desc = m.description
    _audit(db, desc, "remove", user, m, item_brief=m.item_brief)
    desc.mappings.remove(m)
    _settle(desc)
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
    for field in ("item_no", "item_brief", "cost_sheet_type", "class_name", "cost", "reason", "flags", "source", "status"):
        setattr(m, field, getattr(fresh, field))
    m.scope_label = m.scope_label or fresh.scope_label
    _audit(db, desc, "replace", user, m, previous=previous, note=body.note)
    db.commit()
    return m


@router.post("/descriptions/{did}/mappings", response_model=s.MappingOut)
def add_mapping(did: int, body: s.AddMapping, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
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
    db.commit()
    return desc


@router.post("/descriptions/{did}/regenerate", response_model=s.DescriptionOut)
def regenerate(did: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    desc = _desc(db, did)
    jobs.regenerate(db, desc, user.id)
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
        "boq_files": db.scalar(select(func.count(BOQUpload.id))) or 0,
        "parent_descriptions": db.scalar(select(func.count(BOQDescription.id)).where(
            BOQDescription.status.notin_(("skipped", "mapped")))) or 0,
        "components_mapped": m[0] or 0, "approved": m[1] or 0, "pending": m[2] or 0, "low_confidence": m[3] or 0,
        "recent": [{"id": j.id, "filename": j.boq.filename, "status": j.status, "mode": j.mode, "total": j.total,
                    "processed": j.processed, "created_at": j.created_at} for j in recent],
    }


_ = build_component  # re-exported for tests that build records directly
