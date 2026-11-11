"""Background processing, review operations and export orchestration."""
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.tables import (
    BOQDescription, ComponentMapping, ExportRecord, ItemMasterComponent, ItemMasterUpload, MappingApproval,
    ProcessingHistory, now,
)
from app.services.ai_matching.engine import LineResult, MatchConfig, MatchingEngine
from app.services.ai_matching.requirements import build_requirement, precedent_key
from app.services.ai_matching.retrieval import MasterIndexes
from app.services.boq_parser.parser import SalesLine, parse_boq
from app.services.excel_export import reports
from app.services.excel_export.exporter import ChildPlan, export_boq
from app.services.excel_export.verifier import verify_export
from app.services.item_master.processor import Component, build_component, data_quality, read_item_master
from app.services.validation import validator

log = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="boq-job")
_index_lock = threading.Lock()


@lru_cache(maxsize=3)
def _load_indexes(upload_id: int) -> MasterIndexes:
    with SessionLocal() as db:
        rows = db.scalars(select(ItemMasterComponent).where(ItemMasterComponent.upload_id == upload_id)).all()
        return MasterIndexes([build_component(r.raw) for r in rows])


def indexes_for(upload_id: int) -> MasterIndexes:
    with _index_lock:
        return _load_indexes(upload_id)


def ingest_item_master(upload_id: int) -> None:
    """Parse a stored Item Master into the database, off the request thread."""
    with SessionLocal() as db:
        upload = db.get(ItemMasterUpload, upload_id)
        try:
            components = read_item_master(upload.stored_path)
            seen: set[str] = set()
            batch = []
            for c in components:
                if c.item_no in seen:
                    continue
                seen.add(c.item_no)
                batch.append({"upload_id": upload_id, "item_no": c.item_no, "item_brief": c.item_brief,
                              "cost_sheet_type": c.cost_sheet_type, "uom": c.uom, "cost": c.cost,
                              "family": c.family, "raw": c.raw})
            db.bulk_insert_mappings(ItemMasterComponent, batch)
            upload.record_count = len(batch)
            upload.quality = data_quality(components)
            upload.status = "ready"
        except Exception as e:  # surfaced to the user on the upload record
            log.exception("Item Master ingest failed")
            db.rollback()
            upload = db.get(ItemMasterUpload, upload_id)
            upload.status, upload.error = "failed", str(e)
        db.commit()


def _store_result(db, job: ProcessingHistory, line: SalesLine, result: LineResult, threshold: int,
                  require_manual: bool) -> BOQDescription:
    desc = BOQDescription(
        job_id=job.id, row_number=line.row.row, unique_no=line.row.unique_no, description=line.row.text,
        context=line.context, cost_sheet_type=line.row.cost_sheet_type, uom=line.row.uom,
        quantity=line.row.billable_qty, status=result.status, skip_reason=result.skip_reason,
        requirements=result.requirement.as_dict(), candidates=result.candidates, unmatched=result.unmatched,
        notes=result.notes, engine=result.engine,
        existing_children=[{"item_no": c.item_no, "item_brief": c.item_brief, "cost_sheet_type": c.cost_sheet_type,
                            "class_name": c.class_name, "unique_no": c.unique_no} for c in line.children],
    )
    for s in result.selections:
        c = s.component
        desc.mappings.append(ComponentMapping(
            item_no=c.item_no, item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type, class_name=c.class_name,
            cost=c.cost, confidence=s.confidence, selection_type=s.selection_type, reason=s.reason,
            scope_label=s.scope_label, flags=s.flags, source=s.source,
            status=initial_status(s.confidence, s.flags, s.selection_type, threshold, require_manual)))
    db.add(desc)
    return desc


def initial_status(confidence: int, flags: list[str], selection_type: str, threshold: int, require_manual: bool) -> str:
    """Nothing is approved automatically. 'review' marks what must not be bulk-approved."""
    blocking = [f for f in flags if not f.startswith(("Item Master cost", "Item does not state"))]
    if blocking or selection_type == "Inferred":
        return "review"
    if require_manual and confidence < threshold:
        return "review"
    return "pending"


def load_precedents(db) -> dict[str, dict[str, int]]:
    """Engineers' decisions on identical BOQ lines: +1 approved, -1 marked wrong or replaced.

    Keyed by the exact line text and its section, never fuzzily, because the right component
    can depend on size, model or project. A precedent adds weight; it never bypasses the
    technical checks, so an approved item that conflicts with the line is still rejected.
    """
    votes: dict[str, dict[str, int]] = {}

    def vote(desc: str, ctx: list, item_no: str | None, value: int) -> None:
        if item_no:
            bucket = votes.setdefault(precedent_key(desc, ctx or []), {})
            bucket[item_no] = bucket.get(item_no, 0) + value

    rows = db.execute(select(BOQDescription.description, BOQDescription.context, ComponentMapping.item_no,
                             ComponentMapping.status)
                      .join(ComponentMapping, ComponentMapping.description_id == BOQDescription.id)
                      .where(ComponentMapping.status.in_(("approved", "incorrect"))))
    for desc, ctx, item_no, status in rows:
        vote(desc, ctx, item_no, 1 if status == "approved" else -1)
    replaced = db.execute(select(BOQDescription.description, BOQDescription.context, MappingApproval.previous_item_no)
                          .join(MappingApproval, MappingApproval.description_id == BOQDescription.id)
                          .where(MappingApproval.action == "replace"))
    for desc, ctx, item_no in replaced:
        vote(desc, ctx, item_no, -1)
    return {k: {n: (1 if v > 0 else -1) for n, v in b.items() if v} for k, b in votes.items()}


def run_job(job_id: int, ai_client=None) -> None:
    with SessionLocal() as db:
        job = db.get(ProcessingHistory, job_id)
        job.status = "running"
        db.commit()
        try:
            cfg = job.config
            parsed = parse_boq(job.boq.stored_path, job.sheet)
            indexes = indexes_for(job.item_master_upload_id)
            engine = MatchingEngine(indexes, MatchConfig(
                selected_types=cfg["cost_sheet_types"], max_candidates=cfg["max_candidates"],
                suggest_inferred=cfg["suggest_inferred"], mode=job.mode,
                precedents=load_precedents(db)), ai_client=ai_client)
            job.total = len(parsed.lines)
            job.summary = {"warnings": parsed.warnings}
            db.commit()
            counts: dict[str, int] = {}
            for line in parsed.lines:
                if line.is_mapped and not cfg.get("reprocess_mapped"):
                    req = build_requirement(line)
                    result = LineResult(req, in_scope=False, skip_reason="Already has components; preserved")
                    desc = _store_result(db, job, line, result, cfg["confidence_threshold"], True)
                    desc.status = "mapped"
                else:
                    result = engine.match_line(line)
                    desc = _store_result(db, job, line, result, cfg["confidence_threshold"],
                                         cfg["manual_low_confidence"])
                key = f"{desc.status}:{line.row.cost_sheet_type}"
                counts[key] = counts.get(key, 0) + 1
                job.processed += 1
                db.commit()
            job.summary = {**job.summary, "counts": counts}
            job.status = "completed"
        except Exception as e:
            log.exception("Job %s failed", job_id)
            db.rollback()
            job = db.get(ProcessingHistory, job_id)
            job.status, job.error = "failed", str(e)
        job.finished_at = now()
        db.commit()


def regenerate(db, desc: BOQDescription, user_id: int | None, ai_client=None) -> None:
    """Re-run one line, honouring engineer-edited requirements."""
    job = db.get(ProcessingHistory, desc.job_id)
    parsed = parse_boq(job.boq.stored_path, job.sheet)
    line = next(l for l in parsed.lines if l.row.row == desc.row_number)
    cfg = job.config
    engine = MatchingEngine(indexes_for(job.item_master_upload_id), MatchConfig(
        selected_types=cfg["cost_sheet_types"], max_candidates=cfg["max_candidates"],
        suggest_inferred=cfg["suggest_inferred"], mode=job.mode,
                precedents=load_precedents(db)), ai_client=ai_client)
    if desc.requirements_edited:
        note = desc.requirements.get("engineer_note") or ""
        if desc.requirements.get("specification"):
            note = f"{note} {desc.requirements['specification']}"
        if note.strip():
            # Leading, so an engineer's figure wins over the one in the BOQ. Local to this
            # parse: the workbook's description is never changed.
            line.row.description = f"{note.strip()} {line.row.text}"
    result = engine.match_line(line)
    kept = {m.item_no for m in desc.mappings if m.source == "manual" or m.status == "approved"}
    db.add(MappingApproval(job_id=job.id, description_id=desc.id, action="regenerate", user_id=user_id,
                           detail={"previous": [m.item_no for m in desc.mappings], "kept": sorted(kept)}))
    desc.mappings = [m for m in desc.mappings if m.item_no in kept]
    for s in result.selections:
        if s.component.item_no in kept:
            continue
        c = s.component
        desc.mappings.append(ComponentMapping(
            item_no=c.item_no, item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type, class_name=c.class_name,
            cost=c.cost, confidence=s.confidence, selection_type=s.selection_type, reason=s.reason,
            scope_label=s.scope_label, flags=s.flags, source=s.source,
            status=initial_status(s.confidence, s.flags, s.selection_type, cfg["confidence_threshold"],
                                  cfg["manual_low_confidence"])))
    if not desc.requirements_edited:
        desc.requirements = result.requirement.as_dict()
    desc.candidates, desc.unmatched, desc.notes = result.candidates, result.unmatched, result.notes
    desc.status = result.status
    desc.processed_at = now()


def mapping_from_master(c: Component, confidence: int, reason: str) -> ComponentMapping:
    return ComponentMapping(item_no=c.item_no, item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type,
                            class_name=c.class_name, cost=c.cost, confidence=confidence, selection_type="Explicit",
                            reason=reason, scope_label="Manual selection", flags=validator.component_flags(c),
                            source="manual", status="approved")


def run_export(db, job: ProcessingHistory) -> ExportRecord:
    settings = get_settings()
    descs = db.scalars(select(BOQDescription).where(BOQDescription.job_id == job.id)
                       .order_by(BOQDescription.row_number)).all()
    indexes = indexes_for(job.item_master_upload_id)
    errors: list[str] = []
    plans: list[ChildPlan] = []
    report_rows: list[dict] = []
    unmatched_rows: list[dict] = []
    approved = pending = 0
    for d in descs:
        for m in d.mappings:
            report_rows.append({"row": d.row_number, "unique_no": d.unique_no, "description": d.description,
                                "context": " > ".join(d.context), "item_no": m.item_no, "item_brief": m.item_brief,
                                "cost_sheet_type": m.cost_sheet_type, "class_name": m.class_name,
                                "confidence": m.confidence, "selection_type": m.selection_type, "status": m.status,
                                "source": m.source, "reason": m.reason, "flags": "; ".join(m.flags)})
            if m.status == "approved":
                approved += 1
                errors.extend(validator.validate_for_export(m.item_no, m.cost_sheet_type, indexes.by_no))
                master = indexes.by_no.get(m.item_no)
                if master:  # values always come from the master, never from the stored mapping
                    plans.append(ChildPlan(d.row_number, master.item_no, master.item_brief, master.cost_sheet_type,
                                           master.class_name, master.cost))
            elif m.status != "incorrect":
                pending += 1
        gaps = list(d.unmatched)
        if d.flagged:
            gaps.append(f"Flagged for manual review: {d.flag_note}".rstrip(": "))
        if d.status not in ("skipped", "mapped") and not d.mappings and not gaps:
            gaps.append("No components selected")
        for gap in gaps:
            unmatched_rows.append({"row": d.row_number, "unique_no": d.unique_no, "description": d.description,
                                   "context": " > ".join(d.context), "cost_sheet_type": d.cost_sheet_type,
                                   "status": d.status, "gap": gap})

    summary = {
        "descriptions_processed": sum(d.status not in ("skipped", "mapped") for d in descs),
        "descriptions_skipped": sum(d.status == "skipped" for d in descs),
        "descriptions_preserved": sum(d.status == "mapped" for d in descs),
        "components_selected": len(report_rows), "approved_mappings": approved, "pending_mappings": pending,
        "unmatched_descriptions": len({r["row"] for r in unmatched_rows}),
    }
    record = ExportRecord(job_id=job.id, status="failed", summary=summary)
    db.add(record)
    db.flush()
    folder = settings.storage_dir / "exports" / str(record.id)
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(job.boq.stored_path)
    out = folder / f"{source.stem}_mapped{source.suffix}"
    if errors:
        record.verification = {"ok": False, "errors": errors, "checks": {}}
    else:
        try:
            info = export_boq(source, job.sheet, plans, out)
            result = verify_export(source, out, job.sheet, info, indexes.by_no)
            record.verification = result
            if result["ok"]:
                record.status = "verified"
                record.boq_path = str(out)
            else:
                out.unlink(missing_ok=True)
        except Exception as e:  # never leave a half-written workbook available
            log.exception("Export failed")
            out.unlink(missing_ok=True)
            record.verification = {"ok": False, "errors": [str(e)], "checks": {}}
    map_path, gap_path = folder / "component_mapping_report.xlsx", folder / "unmatched_components_report.xlsx"
    reports.mapping_report(map_path, report_rows)
    reports.unmatched_report(gap_path, unmatched_rows)
    record.mapping_report_path, record.unmatched_report_path = str(map_path), str(gap_path)
    db.commit()
    return record


def store_upload(content: bytes, kind: str, filename: str) -> Path:
    """Uploads go to a fresh folder, so an original is never overwritten."""
    import uuid
    folder = get_settings().storage_dir / "uploads" / kind / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(content)
    return path


def delete_upload_folder(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)
