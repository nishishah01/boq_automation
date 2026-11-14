"""Background processing, review operations and export orchestration."""
import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.tables import (
    BOQDescription, ComponentMapping, ExportRecord, ItemMasterComponent, ItemMasterUpload, MappingApproval,
    ProcessingHistory, now,
)
from app.services.ai_matching.engine import LineResult, MatchConfig, MatchingEngine, Selection
from app.services.ai_matching.providers import AI_MODES
from app.services.ai_matching.requirements import build_requirement, precedent_key
from app.services.ai_matching.retrieval import MasterIndexes
from app.services.boq_parser import eligibility
from app.services.boq_parser.parser import SalesLine, parse_boq
from app.services.excel_export import reports
from app.services.excel_export.exporter import ChildPlan, export_boq
from app.services.excel_export.verifier import verify_export
from app.services.item_master.processor import Component, build_component, data_quality, read_item_master
from app.services.knowledge import service as knowledge
from app.services.validation import validator

log = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="boq-job")
_index_lock = threading.Lock()


@lru_cache(maxsize=3)
def _load_components(upload_id: int) -> tuple[Component, ...]:
    with SessionLocal() as db:
        rows = db.scalars(select(ItemMasterComponent).where(ItemMasterComponent.upload_id == upload_id)).all()
        return tuple(build_component(r.raw) for r in rows)


@lru_cache(maxsize=3)
def _load_indexes(upload_id: int, overrides: frozenset) -> MasterIndexes:
    return MasterIndexes(list(_load_components(upload_id)), dict(overrides))


def indexes_for(upload_id: int, kb=None) -> MasterIndexes:
    """Search indexes for one Item Master, with records filed under the Cost Sheet Type that
    verified knowledge gives them. Rebuilt only when such a classification changes."""
    kb = kb if kb is not None else knowledge.snapshot()
    with _index_lock:
        components = _load_components(upload_id)
        overrides = kb.classifier.overrides({c.item_no: c.cost_sheet_type for c in components}) if not kb.empty else {}
        return _load_indexes(upload_id, frozenset(overrides.items()))


def warm_indexes(indexes: MasterIndexes, cost_sheet_types: list[str]) -> None:
    """Build the search index for each type up front (the slow part of a first run)."""
    for t in cost_sheet_types:
        indexes.index(t)


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
        ready = upload.status == "ready"
    if ready:
        with SessionLocal() as db:  # historical BOQs that were waiting for an Item Master
            for hid in knowledge.stale_after_new_master(db):
                knowledge.submit(hid)
        # Build the search indexes now, while the user is still setting up the run, so the
        # first job does not wait for them. A failure here only means the job builds them.
        try:
            warm_indexes(indexes_for(upload_id), get_settings().enabled_types)
        except Exception:
            log.exception("Pre-building search indexes for Item Master %s failed", upload_id)


def initial_status(confidence: int, flags: list[str], selection_type: str, threshold: int, require_manual: bool) -> str:
    """Nothing is approved automatically. 'review' marks what must not be bulk-approved."""
    blocking = [f for f in flags if not f.startswith(("Item Master cost", "Item does not state"))]
    if blocking or selection_type == "Inferred":
        return "review"
    if require_manual and confidence < threshold:
        return "review"
    return "pending"


def mapping_from_selection(s: Selection, threshold: int, require_manual: bool) -> ComponentMapping:
    c, cls = s.component, s.classification
    return ComponentMapping(
        item_no=c.item_no, item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type, class_name=c.class_name,
        cost=c.cost, confidence=s.confidence, selection_type=s.selection_type, reason=s.reason,
        scope_label=s.scope_label, flags=s.flags, source=s.source,
        status=initial_status(s.confidence, s.flags, s.selection_type, threshold, require_manual),
        master_cost_sheet_type=c.original_cost_sheet_type, ai_cost_sheet_type=c.cost_sheet_type,
        classification_source=cls.source if cls else "item_master", classification_note=cls.note if cls else "",
        evidence=s.evidence)


def _store_result(db, job: ProcessingHistory, line: SalesLine, result: LineResult, threshold: int,
                  require_manual: bool) -> BOQDescription:
    desc = BOQDescription(
        job_id=job.id, row_number=line.row.row, unique_no=line.row.unique_no, description=line.row.text,
        context=line.context, cost_sheet_type=line.row.cost_sheet_type, uom=line.row.uom,
        quantity=line.row.billable_qty, status=result.status, skip_reason=result.skip_reason,
        requirements={**result.requirement.as_dict(), "historical_examples": result.history},
        candidates=result.candidates, unmatched=result.unmatched,
        notes=result.notes, engine=result.engine, flagged=bool(result.scope_note), flag_note=result.scope_note,
        existing_children=[{"item_no": c.item_no, "item_brief": c.item_brief, "cost_sheet_type": c.cost_sheet_type,
                            "class_name": c.class_name, "unique_no": c.unique_no} for c in line.children],
    )
    for s in result.selections:
        desc.mappings.append(mapping_from_selection(s, threshold, require_manual))
    db.add(desc)
    return desc


def load_precedents(db) -> dict[str, dict[str, int]]:
    """Engineers' decisions on identical BOQ lines: +1 approved, -1 marked wrong or replaced.

    Keyed by the exact line text and its section, never fuzzily, because the right component
    can depend on size, model or project. A precedent adds weight; it never bypasses the
    technical checks, so an approved item that conflicts with the line is still rejected.
    Similar (not identical) lines learn through the knowledge base instead.
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


def boq_layout(boq, sheet: str) -> dict | None:
    """The confirmed or detected layout of one worksheet of an upload (None: detect from the file)."""
    entry = next((x for x in boq.sheets or [] if x.get("name") == sheet), None)
    return entry.get("layout") if entry else None


def _engine(db, job: ProcessingHistory, ai_client=None, failure_limit: int = 0) -> MatchingEngine:
    cfg = job.config
    kb = knowledge.snapshot()
    return MatchingEngine(indexes_for(job.item_master_upload_id, kb), MatchConfig(
        selected_types=cfg["cost_sheet_types"], max_candidates=cfg["max_candidates"],
        suggest_inferred=cfg["suggest_inferred"], mode=job.mode, precedents=load_precedents(db),
        knowledge=None if kb.empty else kb), ai_client=ai_client, ai_failure_limit=failure_limit)


def run_job(job_id: int, ai_client=None) -> None:
    with SessionLocal() as db:
        job = db.get(ProcessingHistory, job_id)
        job.status = "running"
        db.commit()
        try:
            cfg = job.config
            settings = get_settings()
            parsed = parse_boq(job.boq.stored_path, job.sheet, boq_layout(job.boq, job.sheet))
            engine = _engine(db, job, ai_client, settings.ai_failure_limit)
            kb = engine.config.knowledge
            job.total = len(parsed.lines)
            job.summary = {"warnings": parsed.warnings, "knowledge": {
                "historical_descriptions": sum(p.source == "historical" for p in kb.parents) if kb else 0,
                "approved_descriptions": sum(p.source != "historical" for p in kb.parents) if kb else 0}}
            db.commit()
            warm_indexes(engine.indexes, cfg["cost_sheet_types"])
            counts: dict[str, int] = {}
            threshold = cfg["confidence_threshold"]

            def store(line: SalesLine, result: LineResult, status: str = "", manual: bool | None = None) -> None:
                desc = _store_result(db, job, line, result, threshold,
                                     cfg["manual_low_confidence"] if manual is None else manual)
                if status:
                    desc.status = status
                key = f"{desc.status}:{line.row.cost_sheet_type}"
                counts[key] = counts.get(key, 0) + 1
                job.processed += 1
                db.commit()

            to_match, flags = [], {}
            zero_rule = cfg.get("zero_quantity", "review")
            skipped_unit_qty = zero_qty = 0
            for line in parsed.lines:
                if line.is_mapped and not cfg.get("reprocess_mapped"):
                    req = build_requirement(line)
                    store(line, LineResult(req, in_scope=False, skip_reason="Already has components; preserved"),
                          "mapped", True)
                    continue
                # Mandatory: unit AND quantity on this line itself, or it is never matched.
                ok = eligibility.check(line.row.uom, line.row.qty_state, line.row.qty_raw, zero_rule)
                if not ok.eligible:
                    skipped_unit_qty += 1
                    store(line, LineResult(build_requirement(line), in_scope=False, skip_reason=ok.reason),
                          "incomplete", True)
                    continue
                if ok.flag:
                    zero_qty += 1
                    flags[line.row.row] = ok.flag
                to_match.append(line)
            job.summary = {**job.summary, "eligibility": {
                "identified": len(parsed.lines), "eligible": len(to_match), "skipped_unit_qty": skipped_unit_qty,
                "zero_quantity": zero_qty, "zero_quantity_rule": zero_rule}}
            db.commit()
            # Each AI call is mostly waiting on the network, so lines run side by side. The
            # database is only touched from this thread; rows are listed by row number, so the
            # order they finish in does not matter.
            workers = max(1, settings.ai_concurrency) if job.mode in AI_MODES else 1
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"job{job_id}-line") as pool:
                futures = {pool.submit(engine.match_line, line): line for line in to_match}
                for fut in as_completed(futures):
                    line, result = futures[fut], fut.result()
                    if line.row.row in flags:
                        for sel in result.selections:
                            sel.flags.append(flags[line.row.row])
                        result.notes.append(flags[line.row.row])
                    store(line, result)
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
    parsed = parse_boq(job.boq.stored_path, job.sheet, boq_layout(job.boq, job.sheet))
    line = next(l for l in parsed.lines if l.row.row == desc.row_number)
    cfg = job.config
    engine = _engine(db, job, ai_client)
    if desc.requirements_edited:
        note = desc.requirements.get("engineer_note") or ""
        if desc.requirements.get("specification"):
            note = f"{note} {desc.requirements['specification']}"
        if note.strip():
            # Leading, so an engineer's figure wins over the one in the BOQ. Local to this
            # parse: the workbook's description is never changed.
            line.row.description = f"{note.strip()} {line.row.text}"
    ok = eligibility.check(line.row.uom, line.row.qty_state, line.row.qty_raw, cfg.get("zero_quantity", "review"))
    if not ok.eligible:  # the BOQ is the same file, so this only guards against a changed rule
        raise ValueError(ok.reason)
    result = engine.match_line(line)
    if ok.flag:
        for sel in result.selections:
            sel.flags.append(ok.flag)
    kept = {m.item_no for m in desc.mappings if m.source == "manual" or m.status == "approved"}
    db.add(MappingApproval(job_id=job.id, description_id=desc.id, action="regenerate", user_id=user_id,
                           detail={"previous": [m.item_no for m in desc.mappings], "kept": sorted(kept)}))
    desc.mappings = [m for m in desc.mappings if m.item_no in kept]
    for s in result.selections:
        if s.component.item_no not in kept:
            desc.mappings.append(mapping_from_selection(s, cfg["confidence_threshold"], cfg["manual_low_confidence"]))
    if not desc.requirements_edited:
        desc.requirements = {**result.requirement.as_dict(), "historical_examples": result.history}
    desc.candidates, desc.unmatched, desc.notes = result.candidates, result.unmatched, result.notes
    desc.status = result.status
    if result.scope_note:
        desc.flagged, desc.flag_note = True, result.scope_note
    desc.processed_at = now()


def mapping_from_master(c: Component, confidence: int, reason: str) -> ComponentMapping:
    """A component an engineer chose by hand, classified by the same priority rule."""
    c, cls = validator.classify(c, knowledge.snapshot())
    return ComponentMapping(item_no=c.item_no, item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type,
                            class_name=c.class_name, cost=c.cost, confidence=confidence, selection_type="Explicit",
                            reason=reason, scope_label="Manual selection", flags=validator.component_flags(c),
                            source="manual", status="approved", master_cost_sheet_type=c.original_cost_sheet_type,
                            ai_cost_sheet_type=c.cost_sheet_type, classification_source=cls.source,
                            classification_note=cls.note, evidence=[])


def _report_row(d: BOQDescription, m: ComponentMapping) -> dict:
    refs = "; ".join(f"{e.get('file')} row {e.get('row')}" for e in (m.evidence or [])[:3])
    return {"row": d.row_number, "unique_no": d.unique_no, "description": d.description,
            "context": " > ".join(d.context), "item_no": m.item_no, "item_brief": m.item_brief,
            "cost_sheet_type": m.cost_sheet_type, "master_cost_sheet_type": m.master_cost_sheet_type,
            "classification_source": m.classification_source, "class_name": m.class_name,
            "confidence": m.confidence, "selection_type": m.selection_type, "status": m.status,
            "source": m.source, "reason": m.reason, "evidence": refs, "flags": "; ".join(m.flags)}


def run_export(db, job: ProcessingHistory) -> ExportRecord:
    settings = get_settings()
    descs = db.scalars(select(BOQDescription).where(BOQDescription.job_id == job.id)
                       .order_by(BOQDescription.row_number)).all()
    by_no = {c.item_no: c for c in _load_components(job.item_master_upload_id)}
    enabled = job.config["cost_sheet_types"]
    errors: list[str] = []
    plans: list[ChildPlan] = []
    report_rows: list[dict] = []
    unmatched_rows: list[dict] = []
    approved = pending = 0

    def gap_row(d: BOQDescription, gap: str) -> dict:
        return {"row": d.row_number, "unique_no": d.unique_no, "description": d.description,
                "context": " > ".join(d.context), "cost_sheet_type": d.cost_sheet_type, "status": d.status, "gap": gap}

    for d in descs:
        if d.status == "incomplete":  # no unit or quantity: never gets components
            unmatched_rows.append(gap_row(d, d.skip_reason))
            continue
        for m in d.mappings:
            report_rows.append(_report_row(d, m))
            if m.status == "approved":
                approved += 1
                errors.extend(validator.validate_for_export(m.item_no, m.cost_sheet_type, by_no, enabled))
                master = by_no.get(m.item_no)
                if master:  # number, brief and class from the master; type is the reviewed recommendation
                    plans.append(ChildPlan(d.row_number, master.item_no, master.item_brief, m.cost_sheet_type,
                                           master.class_name, master.cost))
            elif m.status != "incorrect":
                pending += 1
        gaps = list(d.unmatched)
        if d.flagged:
            gaps.append(f"Flagged for manual review: {d.flag_note}".rstrip(": "))
        if d.status not in ("skipped", "mapped", "incomplete") and not d.mappings and not gaps:
            gaps.append("No components selected")
        unmatched_rows.extend(gap_row(d, gap) for gap in gaps)

    summary = {
        "descriptions_identified": len(descs),
        "descriptions_processed": sum(d.status not in ("skipped", "mapped", "incomplete") for d in descs),
        "descriptions_skipped_unit_qty": sum(d.status == "incomplete" for d in descs),
        "descriptions_skipped": sum(d.status == "skipped" for d in descs),
        "descriptions_preserved": sum(d.status == "mapped" for d in descs),
        "descriptions_scope_review": sum(d.status == "scope_review" for d in descs),
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
            info = export_boq(source, job.sheet, plans, out, boq_layout(job.boq, job.sheet))
            result = verify_export(source, out, job.sheet, info, by_no, enabled)
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
    folder = get_settings().storage_dir / "uploads" / kind / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(content)
    return path


def delete_upload_folder(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)
