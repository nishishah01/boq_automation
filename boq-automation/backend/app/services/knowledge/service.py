"""Knowledge base storage: historical BOQ ingestion (single uploads and folder scans), review
feedback, and the snapshots the matching engine searches.

Pipeline for every historical BOQ (one file at a time, on a single background worker, so a
thousand files are a thousand small transactions and never one enormous request):

    scan folder -> inventory -> copy new/changed files -> queue
    -> read each worksheet in its own layout (extract.py) -> validate parent/component links
    -> keep Highside Supply / Highside Installation as knowledge, record the rest as out of scope
    -> store parents + components (knowledge_parents / knowledge_components)
    -> rebuild the vector index (index.py, lazily, from the stored rows) -> status

A BOQ counts as learned only once its mappings are stored; its status says so ("ingested" or
"partial"). Nothing in a source folder is ever modified: files are read once, hashed and
copied into storage, and every later step works on the copy.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.cost_sheets import REGISTRY
from app.database.session import SessionLocal
from app.models.tables import (
    BOQDescription, HistoricalBOQ, HistoricalScan, ItemMasterComponent, ItemMasterUpload, KnowledgeComponent,
    KnowledgeParent, now,
)
from app.services.item_master.normalize import Attrs

from .extract import extract_workbook
from .index import KnowledgeSnapshot, KnownComponent, KnownParent, SnapshotCache, parent_attrs

log = logging.getLogger(__name__)
cache = SnapshotCache()
_hist_lock = threading.Lock()
_hist_parents: tuple[int, list[KnownParent]] | None = None  # (historical version, parsed parents)

# One worker: SQLite has one writer, and a steady file-by-file pace keeps every file's result
# in its own transaction. Mapping runs use their own executor and are never blocked by this.
ingest_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-ingest")

SUPPORTED = (".xlsx", ".xlsm")
KNOWN_UNSUPPORTED = {".xls": "Legacy Excel 97-2003 (.xls) format is not supported; save it as .xlsx to ingest it",
                     ".xlsb": "Binary Excel (.xlsb) format is not supported; save it as .xlsx to ingest it",
                     ".msg": "Outlook message, not a BOQ workbook", ".pdf": "PDF document, not a BOQ workbook",
                     ".csv": "CSV file, not a BOQ workbook"}
STORED = ("ingested", "partial")  # statuses whose mappings are in the knowledge base
BUSY = ("queued", "processing")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def latest_master(db) -> ItemMasterUpload | None:
    return db.scalar(select(ItemMasterUpload).where(ItemMasterUpload.status == "ready")
                     .order_by(ItemMasterUpload.id.desc()))


def _master_types(db, upload_id: int | None) -> dict[str, tuple[str, str]]:
    """Item No (upper) -> (Item Master spelling, Cost Sheet Type)."""
    if upload_id is None:
        return {}
    rows = db.execute(select(ItemMasterComponent.item_no, ItemMasterComponent.cost_sheet_type)
                      .where(ItemMasterComponent.upload_id == upload_id))
    return {no.upper(): (no, t) for no, t in rows}


def store_copy(content: bytes, filename: str) -> Path:
    """Historical files are kept as their own read-only copies, in a fresh folder each."""
    folder = get_settings().storage_dir / "uploads" / "historical" / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(content)
    return path


_queued = 0
_queued_lock = threading.Lock()


def submit(hid: int) -> None:
    """Queue one file. When the last queued file is done, the search index is rebuilt in the
    background, so the next mapping run finds it ready (uploads need no separate step)."""
    global _queued
    with _queued_lock:
        _queued += 1
    ingest_executor.submit(_ingest_and_index, hid)


def _ingest_and_index(hid: int) -> None:
    global _queued
    try:
        ingest_historical(hid)
    finally:
        with _queued_lock:
            _queued -= 1
            idle = _queued == 0
        if idle:
            try:
                warm_index()
            except Exception:  # the index is rebuilt on the next run anyway
                log.exception("Rebuilding the knowledge index failed")


# ---------------------------------------------------------------- one file

def _resolve(hist_type: str, master_type: str, enabled: list[str]) -> tuple[str, str, bool]:
    """(resolved Cost Sheet Type, where it came from, in the enabled scope).

    The verified BOQ's own classification wins; the Item Master's fills in only where the
    engineer left the cell blank. A type the registry does not know ("Section", "Mathadi",
    "Miscellaneous Items") is kept as written and is out of scope."""
    if hist_type:
        return hist_type, "historical", hist_type in enabled and hist_type in REGISTRY
    if master_type:
        return master_type, "item_master", master_type in enabled
    return "", "none", False


def ingest_historical(hid: int) -> None:
    """Extract, validate and store one historical BOQ's mappings. Runs off the request thread;
    an error is recorded on the file and never affects any other file."""
    with SessionLocal() as db:
        h = db.get(HistoricalBOQ, hid)
        if h is None or h.status == "duplicate":
            return
        h.status, h.error = "processing", None
        h.attempts = (h.attempts or 0) + 1
        db.commit()
        try:
            _ingest(db, h)
        except Exception as e:  # surfaced on the record, never raised to a request
            log.exception("Historical BOQ %s failed", hid)
            db.rollback()
            h = db.get(HistoricalBOQ, hid)
            h.status, h.error = "failed", f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            h.parents_count = h.components_count = 0
            db.execute(delete(KnowledgeParent).where(KnowledgeParent.historical_id == h.id))
        h.processed_at = now()
        db.commit()
    cache.bump(historical=True)


def _ingest(db, h: HistoricalBOQ) -> None:
    enabled = get_settings().enabled_types
    master = latest_master(db)
    master_types = _master_types(db, master.id if master else None)
    if not Path(h.stored_path).exists():
        raise FileNotFoundError(f"The stored copy of this file is missing ({h.stored_path}); upload or rescan it")
    found = extract_workbook(h.stored_path, set(master_types))
    fingerprint = found.fingerprint
    db.execute(delete(KnowledgeParent).where(KnowledgeParent.historical_id == h.id))  # safe reprocessing

    summary = {"sheets": found.sheets, "warnings": found.warnings[:20], "item_master": master.filename if master else None,
               "repaired": found.repaired, "orphan_components": found.orphans, "extracted_at": now().isoformat()}
    h.fingerprint = fingerprint
    h.parents_count = h.components_count = 0
    h.highside_supply_count = h.highside_installation_count = h.out_of_scope_count = 0
    if not found.parents:
        h.status = "no_mappings"
        h.error = _no_mapping_reason(found)
        if master is None:
            h.error += " No Item Master is loaded yet; it will be reprocessed automatically once one is."
        h.summary = summary
        return
    twin = db.scalar(select(HistoricalBOQ).where(HistoricalBOQ.fingerprint == fingerprint, HistoricalBOQ.id != h.id,
                                                 HistoricalBOQ.status.in_(STORED)))
    if twin is not None:
        h.status, h.duplicate_of = "duplicate", twin.id
        h.error = (f"Same parent-component mappings as '{twin.filename}' (#{twin.id}), which is already in the "
                   "knowledge base; nothing was added twice.")
        h.summary = summary
        return

    by_type: dict[str, int] = {}
    unknown_in_scope, differs, from_master, parents_in_scope = 0, 0, 0, 0
    item_nos: set[str] = set()
    for p in found.parents:
        kp = KnowledgeParent(source="historical", historical_id=h.id, filename=h.filename, sheet=p.sheet,
                             row_number=p.row, unique_no=p.unique_no[:40], description=p.description,
                             context=p.context, cost_sheet_type=p.cost_sheet_type[:60], uom=p.uom[:20],
                             quantity=p.quantity, verified=h.verified, method=p.method,
                             attrs=_attrs_record(parent_attrs(p.description, p.context)))
        any_in = False
        for c in p.components:
            key = c.item_no.upper()
            spelled, master_type = master_types.get(key, (c.item_no, ""))
            resolved, source, in_scope = _resolve(c.cost_sheet_type, master_type, enabled)
            in_master = key in master_types if master_types else True
            any_in |= in_scope
            if in_scope:
                item_nos.add(spelled)
                by_type[resolved] = by_type.get(resolved, 0) + 1
                unknown_in_scope += not in_master
                from_master += source == "item_master"
                differs += bool(c.cost_sheet_type and master_type and master_type != c.cost_sheet_type)
            else:
                h.out_of_scope_count += 1
            kp.components.append(KnowledgeComponent(
                item_no=spelled[:40], item_brief=c.item_brief, cost_sheet_type=c.cost_sheet_type[:60],
                class_name=c.class_name[:20], master_cost_sheet_type=master_type, resolved_cost_sheet_type=resolved[:60],
                type_source=source, in_scope=in_scope, in_master=in_master, row_number=c.row, uom=c.uom[:20],
                quantity=c.quantity))
        kp.in_scope = any_in
        parents_in_scope += any_in
        db.add(kp)

    h.parents_count = parents_in_scope
    h.highside_supply_count = by_type.get("Highside Supply", 0)
    h.highside_installation_count = by_type.get("Highside Installation", 0)
    h.components_count = h.highside_supply_count + h.highside_installation_count
    # Partial = information that should have been learned could not be: a Highside component
    # row with no parent description, or a worksheet that could not be read at all.
    partial, notes = [], []
    lost_in_scope = []
    for o in found.orphan_rows:
        master_type = master_types.get(o["item_no"].upper(), ("", ""))[1]
        if _resolve(o["cost_sheet_type"], master_type, enabled)[2]:
            lost_in_scope.append(o)
    if lost_in_scope:
        rows = ", ".join(f"{o['sheet']}!{o['row']} ({o['item_no']})" for o in lost_in_scope[:5])
        partial.append(f"{len(lost_in_scope)} Highside component row(s) sit under no BOQ description and could not "
                       f"be learned: {rows}")
    if found.orphans > len(lost_in_scope):
        notes.append(f"{found.orphans - len(lost_in_scope)} out-of-scope component row(s) (overheads, Lowside) sit "
                     "directly under a section; not needed for Highside knowledge")
    unread = [s["name"] for s in found.sheets if s["note"].startswith("Worksheet could not be read")]
    if unread:
        partial.append(f"Worksheet(s) could not be read: {', '.join(unread)}")
    if unknown_in_scope:
        notes.append(f"{unknown_in_scope} Highside component(s) use Item Numbers that are not in the current Item "
                     "Master (for example IN- installation codes); they are stored for traceability but cannot be "
                     "suggested until the Item Master contains them")
    summary.update(by_cost_sheet_type=by_type, not_in_item_master=unknown_in_scope,
                   classification_differs_from_master=differs, classified_from_item_master=from_master,
                   out_of_scope_components=h.out_of_scope_count, parents_total=len(found.parents),
                   unique_item_numbers=len(item_nos), partial_reasons=partial, notes=notes,
                   orphan_rows=found.orphan_rows[:50])
    h.summary = summary
    if not parents_in_scope:
        h.status = "no_mappings"
        h.error = (f"{len(found.parents)} parent descriptions with {h.out_of_scope_count} components were read, but "
                   "none of the components is Highside Supply or Highside Installation. They are kept only to "
                   "stop those items being suggested as Highside; nothing was added to the Highside knowledge.")
        return
    h.status = "partial" if partial else "ingested"
    h.error = "; ".join(partial) if partial else None


def _no_mapping_reason(found) -> str:
    notes = {s["note"] for s in found.sheets if s["note"]}
    if any(n.startswith("A flat list") for n in notes):
        return ("No parent-component mappings: the workbook is a flat component list without the parent BOQ "
                "descriptions, so which component belongs to which item cannot be known.")
    if any("No Item Master numbers" in n or "No column of Item Master" in n for n in notes) and \
            not any(s["format"] == "sales_bom" for s in found.sheets):
        return ("No parent-component mappings: no worksheet contains Item Master numbers (Item No. / Item Code). "
                "This looks like a client, costing (OC) or summary sheet rather than a mapped BOQ.")
    if any(s["format"] == "sales_bom" for s in found.sheets):
        return ("The Sales BOM worksheet has no component rows (ClassName / Item No.) under its BOQ items: the "
                "BOQ was not mapped yet.")
    return "No parent descriptions with mapped components were found in any worksheet."


# ---------------------------------------------------------------- folder scans

def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def start_scan(db, root: str, reprocess_changed: bool = True) -> HistoricalScan:
    path = Path(root.strip().strip('"')).expanduser()
    if not path.is_dir():
        raise ValueError(f"Folder not found or not accessible from the server: {root}")
    scan = HistoricalScan(root_path=str(path.resolve()), reprocess_changed=reprocess_changed)
    db.add(scan)
    db.commit()
    ingest_executor.submit(run_scan, scan.id)
    return scan


def run_scan(scan_id: int) -> None:
    """Walk the folder, build the inventory, register new and changed files, then ingest them
    one by one. Runs on the ingestion worker, so files queued by a scan and by uploads are
    processed in order with no two writers."""
    with SessionLocal() as db:
        scan = db.get(HistoricalScan, scan_id)
        try:
            queue = _inventory(db, scan)
        except Exception as e:
            log.exception("Folder scan %s failed", scan_id)
            db.rollback()
            scan = db.get(HistoricalScan, scan_id)
            scan.status, scan.error, scan.finished_at = "failed", str(e), now()
            db.commit()
            return
    for hid in queue:
        ingest_historical(hid)
        with SessionLocal() as db:
            scan = db.get(HistoricalScan, scan_id)
            scan.processed += 1
            db.commit()
    warm_index()
    with SessionLocal() as db:
        scan = db.get(HistoricalScan, scan_id)
        rows = db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.id.in_(queue))).all() if queue else []
        by_status: dict[str, int] = {}
        for h in rows:
            by_status[h.status] = by_status.get(h.status, 0) + 1
        inv = [dict(e) for e in scan.inventory or []]
        status_of = {h.id: (h.status, h.error) for h in db.scalars(select(HistoricalBOQ).where(
            HistoricalBOQ.id.in_([e["historical_id"] for e in inv if e.get("historical_id")])))}
        for e in inv:
            if e.get("historical_id") in status_of:
                e["status"], e["reason"] = status_of[e["historical_id"]][0], status_of[e["historical_id"]][1] or e.get("reason", "")
        scan.inventory = inv
        added = db.scalar(select(func.count(KnowledgeComponent.id)).join(KnowledgeParent).where(
            KnowledgeParent.historical_id.in_(queue or [-1]), KnowledgeComponent.in_scope.is_(True))) or 0
        scan.summary = {**(scan.summary or {}), "processed_by_status": by_status, "new_highside_mappings": added,
                        "index": index_info()}
        scan.status, scan.finished_at = "completed", now()
        db.commit()


def _inventory(db, scan: HistoricalScan) -> list[int]:
    root = Path(scan.root_path)
    inventory: list[dict] = []
    folders = 0
    seen_hash: dict[str, int] = {}  # sha256 -> historical id, for duplicates inside this scan
    queue: list[int] = []
    counts = dict(files=0, supported=0, unsupported=0, temporary=0, duplicate=0, unreadable=0, new=0, changed=0,
                  unchanged=0)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        folders += 1
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = _rel(path, root)
            folder = _rel(Path(dirpath), root)
            folder = "" if folder == "." else folder
            ext = path.suffix.lower()
            entry = {"relative_path": rel, "folder": folder, "filename": name, "extension": ext or "(none)",
                     "size_bytes": 0, "status": "", "reason": "", "historical_id": None}
            counts["files"] += 1
            try:
                entry["size_bytes"] = path.stat().st_size
            except OSError:
                pass
            if name.startswith("~$"):
                counts["temporary"] += 1
                entry.update(status="ignored", reason="Temporary Excel lock file (~$)")
                inventory.append(entry)
                continue
            if ext not in SUPPORTED:
                counts["unsupported"] += 1
                entry.update(status="unsupported", reason=KNOWN_UNSUPPORTED.get(ext, f"{ext or 'Unknown'} files are not BOQ workbooks"))
                inventory.append(entry)
                continue
            counts["supported"] += 1
            existing = db.scalar(select(HistoricalBOQ).where(HistoricalBOQ.source_path == str(path))
                                 .order_by(HistoricalBOQ.id.desc()))
            try:
                content = path.read_bytes()  # read once; the original is never opened for writing
            except OSError as e:
                counts["unreadable"] += 1
                reason = (f"The file could not be opened ({e.strerror or e}). It may be open in Excel or still "
                          "syncing from OneDrive; retry once it is available.")
                h = existing or HistoricalBOQ(filename=name, stored_path="", size_bytes=entry["size_bytes"], sha256="",
                                              origin="folder", source_path=str(path), relative_path=rel, folder=folder)
                h.status, h.error, h.scan_id, h.processed_at = "failed", reason, scan.id, now()
                db.add(h)
                db.flush()
                entry.update(status="failed", reason=reason, historical_id=h.id)
                inventory.append(entry)
                continue
            digest = sha256(content)
            mtime = path.stat().st_mtime
            if existing is not None and existing.sha256 == digest and existing.status != "failed":
                counts["unchanged"] += 1
                seen_hash.setdefault(digest, existing.id)
                entry.update(status=existing.status, reason="Unchanged since it was last ingested",
                             historical_id=existing.id, change="unchanged")
                inventory.append(entry)
                continue
            twin_id = seen_hash.get(digest)
            if twin_id is None:
                twin = db.scalar(select(HistoricalBOQ).where(HistoricalBOQ.sha256 == digest,
                                                             HistoricalBOQ.status.notin_(("failed", "duplicate")),
                                                             HistoricalBOQ.id != (existing.id if existing else -1)))
                twin_id = twin.id if twin else None
            if twin_id is not None:
                counts["duplicate"] += 1
                twin = db.get(HistoricalBOQ, twin_id)
                reason = f"Byte-identical to '{twin.relative_path or twin.filename}' (#{twin.id}); not processed twice"
                h = existing or HistoricalBOQ(filename=name, origin="folder", source_path=str(path), relative_path=rel,
                                              folder=folder)
                h.stored_path, h.size_bytes, h.sha256, h.source_mtime = twin.stored_path, len(content), digest, mtime
                h.status, h.error, h.duplicate_of, h.scan_id, h.processed_at = "duplicate", reason, twin.id, scan.id, now()
                db.add(h)
                db.flush()
                entry.update(status="duplicate", reason=reason, historical_id=h.id)
                inventory.append(entry)
                continue
            stored = store_copy(content, name)
            if existing is not None:  # changed (or previously failed) at the same path
                change = "changed" if existing.sha256 and existing.status != "failed" else "retry"
                counts["changed" if change == "changed" else "new"] += 1
                old = Path(existing.stored_path) if existing.stored_path else None
                existing.stored_path, existing.size_bytes, existing.sha256 = str(stored), len(content), digest
                existing.source_mtime, existing.scan_id, existing.fingerprint, existing.duplicate_of = mtime, scan.id, "", None
                if change == "changed" and not scan.reprocess_changed:
                    existing.status = "changed"
                    existing.error = "The source file changed since it was ingested; reprocess it to update its mappings"
                else:
                    existing.status, existing.error = "queued", None
                    queue.append(existing.id)
                if old is not None and old.parent != stored.parent and _owned(old) and not _shared(db, old, existing.id):
                    shutil.rmtree(old.parent, ignore_errors=True)
                h = existing
                entry.update(change=change)
            else:
                counts["new"] += 1
                h = HistoricalBOQ(filename=name, stored_path=str(stored), size_bytes=len(content), sha256=digest,
                                  origin="folder", source_path=str(path), relative_path=rel, folder=folder,
                                  scan_id=scan.id, source_mtime=mtime, status="queued")
                db.add(h)
                db.flush()
                queue.append(h.id)
                entry.update(change="new")
            seen_hash[digest] = h.id
            entry.update(status=h.status, historical_id=h.id)
            inventory.append(entry)
    scan.folders_scanned = folders
    scan.files_discovered = counts["files"]
    scan.supported_files = counts["supported"]
    scan.unsupported_files = counts["unsupported"]
    scan.temporary_files = counts["temporary"]
    scan.duplicate_files = counts["duplicate"]
    scan.unreadable_files = counts["unreadable"]
    scan.new_files, scan.changed_files, scan.unchanged_files = counts["new"], counts["changed"], counts["unchanged"]
    scan.to_process = len(queue)
    scan.inventory = inventory
    scan.status = "processing"
    db.commit()
    return queue


def _owned(path: Path) -> bool:
    """Only copies inside our own storage are ever deleted."""
    try:
        path.resolve().relative_to((get_settings().storage_dir / "uploads").resolve())
        return True
    except ValueError:
        return False


def _shared(db, path: Path, except_id: int) -> bool:
    return bool(db.scalar(select(func.count(HistoricalBOQ.id)).where(HistoricalBOQ.stored_path == str(path),
                                                                     HistoricalBOQ.id != except_id)))


def retry_failed(db) -> list[int]:
    """Queue every failed file again. A folder file whose original is readable again is
    re-copied first, so a file that was locked during the scan is picked up."""
    ids = []
    for h in db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.status == "failed")).all():
        if h.source_path:
            try:
                content = Path(h.source_path).read_bytes()
            except OSError as e:
                h.error = f"Still unavailable: {e.strerror or e}"
                continue
            if sha256(content) != h.sha256 or not Path(h.stored_path or "").exists():
                h.stored_path, h.sha256, h.size_bytes = str(store_copy(content, h.filename)), sha256(content), len(content)
        h.status, h.error = "queued", None
        ids.append(h.id)
    db.commit()
    for hid in ids:
        submit(hid)
    return ids


def resume_pending() -> None:
    """Files queued or mid-way when the server stopped are queued again on start-up."""
    with SessionLocal() as db:
        rows = db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.status.in_(BUSY))).all()
        for h in rows:
            h.status = "queued"
        for scan in db.scalars(select(HistoricalScan).where(HistoricalScan.status.in_(("scanning", "processing")))):
            scan.status, scan.error, scan.finished_at = "failed", "Server restarted during the scan; its files were requeued", now()
        db.commit()
        ids = [h.id for h in rows]
    for hid in ids:
        submit(hid)


def stale_after_new_master(db) -> list[int]:
    """Historical BOQs read without an Item Master (or that found nothing): worth reading again
    now that one is available, so their Item Numbers and classifications are checked against it."""
    rows = db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.status.in_(STORED + ("no_mappings",)))).all()
    ids = []
    for h in rows:
        if not (h.summary or {}).get("item_master"):
            h.status, h.error = "queued", None
            ids.append(h.id)
    db.commit()
    return ids


def delete_historical(db, h: HistoricalBOQ) -> None:
    db.execute(delete(KnowledgeParent).where(KnowledgeParent.historical_id == h.id))
    for d in db.scalars(select(HistoricalBOQ).where(HistoricalBOQ.duplicate_of == h.id)):
        d.duplicate_of = None  # its copies become ordinary files again
        if d.stored_path == h.stored_path:
            d.status, d.error = "failed", "The file it duplicated was removed; rescan the folder to ingest this copy"
    db.delete(h)
    db.commit()
    cache.bump(historical=True)


# ---------------------------------------------------------------- feedback

def sync_feedback(db, desc: BOQDescription) -> None:
    """Keep one feedback record per reviewed line: exactly its approved components.

    Called after every review action, so the knowledge base always reflects what engineers
    have approved (and stops reflecting what they later rejected or removed)."""
    db.flush()
    db.execute(delete(KnowledgeParent).where(KnowledgeParent.source == "feedback",
                                             KnowledgeParent.description_id == desc.id))
    approved = [m for m in desc.mappings if m.status == "approved"]
    if approved:
        kp = KnowledgeParent(source="feedback", description_id=desc.id, job_id=desc.job_id,
                             filename=f"Review of run #{desc.job_id}", sheet="", row_number=desc.row_number,
                             unique_no=desc.unique_no, description=desc.description, context=desc.context,
                             cost_sheet_type=desc.cost_sheet_type, uom=desc.uom, quantity=desc.quantity,
                             attrs={}, verified=True, in_scope=True, method="review")
        for m in approved:
            kp.components.append(KnowledgeComponent(
                item_no=m.item_no, item_brief=m.item_brief, cost_sheet_type=m.cost_sheet_type,
                class_name=m.class_name, master_cost_sheet_type=m.master_cost_sheet_type or "",
                resolved_cost_sheet_type=m.cost_sheet_type, type_source="feedback", in_scope=True, in_master=True))
        db.add(kp)
    cache.bump()


# ---------------------------------------------------------------- snapshots for matching

def _attrs_record(a: Attrs) -> dict:
    """Readable attributes for the API, plus a lossless copy the index loads without re-parsing."""
    return {**a.as_dict(), "_full": a.dump()}


def _known(p: KnowledgeParent) -> KnownParent:
    full = (p.attrs or {}).get("_full")
    attrs = Attrs.load(full) if full else parent_attrs(p.description, list(p.context or []))
    return KnownParent(p.id, p.source, p.filename, p.sheet, p.row_number, p.description, list(p.context or []),
                       [KnownComponent(c.item_no, c.item_brief, c.cost_sheet_type, c.class_name,
                                       c.resolved_cost_sheet_type or "")
                        for c in p.components], attrs)


def _squash(text: str) -> str:
    return " ".join((text or "").upper().split())


def _identity(p: KnowledgeParent) -> tuple:
    return (_squash(p.description), _squash((p.context or [""])[0]),
            tuple(sorted((c.item_no.upper(), c.cost_sheet_type) for c in p.components)))


def _load_parents() -> list[KnownParent]:
    """Historical parents, with exact repeats collapsed: the same description with the same
    components in a WORKING and a FINAL copy of one project is one example, not two (so it
    does not count double as evidence). Every copy stays in the database for traceability."""
    global _hist_parents
    with SessionLocal() as db:
        version = cache.historical_version
        with _hist_lock:
            if _hist_parents is None or _hist_parents[0] != version:
                rows = db.scalars(select(KnowledgeParent).join(HistoricalBOQ, HistoricalBOQ.id == KnowledgeParent.historical_id)
                                  .where(KnowledgeParent.source == "historical", HistoricalBOQ.status.in_(STORED))
                                  .options(selectinload(KnowledgeParent.components))
                                  .order_by(KnowledgeParent.id)).all()
                unique: dict[tuple, KnownParent] = {}
                for p in rows:
                    key = _identity(p)
                    if key in unique:
                        unique[key].copies += 1
                    else:
                        unique[key] = _known(p)
                _hist_parents = (version, list(unique.values()))
            hist = _hist_parents[1]
        fb = db.scalars(select(KnowledgeParent).where(KnowledgeParent.source != "historical")
                        .options(selectinload(KnowledgeParent.components))).all()
        return hist + [_known(p) for p in fb]


def snapshot() -> KnowledgeSnapshot:
    return cache.get(_load_parents)


_index_state: dict = {}


def warm_index() -> dict:
    """Build the search index (text vectors + embeddings) now, rather than on the next run."""
    import time
    t = time.time()
    kb = snapshot()
    hist = [p for p in kb.parents if p.source == "historical"]
    _index_state.update(built_at=now().isoformat(), seconds=round(time.time() - t, 2), historical_parents=len(hist),
                        feedback_parents=len(kb.parents) - len(hist), version=list(kb.version),
                        embedding_backend=get_settings().embedding_backend)
    return dict(_index_state)


def index_info() -> dict:
    current = [cache.historical_version, cache.feedback_version]
    return {**_index_state, "current_version": current, "up_to_date": _index_state.get("version") == current}


def stats(db) -> dict:
    files = dict(db.execute(select(HistoricalBOQ.status, func.count()).group_by(HistoricalBOQ.status)).all())
    by_type = dict(db.execute(
        select(KnowledgeComponent.resolved_cost_sheet_type, func.count(KnowledgeComponent.id))
        .join(KnowledgeParent, KnowledgeParent.id == KnowledgeComponent.parent_id)
        .join(HistoricalBOQ, HistoricalBOQ.id == KnowledgeParent.historical_id)
        .where(KnowledgeParent.source == "historical", HistoricalBOQ.status.in_(STORED),
               KnowledgeComponent.in_scope.is_(True))
        .group_by(KnowledgeComponent.resolved_cost_sheet_type)).all())
    hist_parents = db.scalar(select(func.count(KnowledgeParent.id)).join(HistoricalBOQ, HistoricalBOQ.id == KnowledgeParent.historical_id)
                             .where(KnowledgeParent.source == "historical", KnowledgeParent.in_scope.is_(True),
                                    HistoricalBOQ.status.in_(STORED))) or 0
    distinct = db.scalar(select(func.count(func.distinct(KnowledgeComponent.item_no)))
                         .join(KnowledgeParent, KnowledgeParent.id == KnowledgeComponent.parent_id)
                         .join(HistoricalBOQ, HistoricalBOQ.id == KnowledgeParent.historical_id)
                         .where(KnowledgeComponent.in_scope.is_(True), HistoricalBOQ.status.in_(STORED))) or 0
    reclassified = db.scalar(select(func.count(func.distinct(KnowledgeComponent.item_no))).where(
        KnowledgeComponent.in_scope.is_(True), KnowledgeComponent.cost_sheet_type != "",
        KnowledgeComponent.master_cost_sheet_type != "",
        KnowledgeComponent.cost_sheet_type != KnowledgeComponent.master_cost_sheet_type)) or 0
    fb_parents = db.scalar(select(func.count(KnowledgeParent.id)).where(KnowledgeParent.source == "feedback")) or 0
    fb_comps = db.scalar(select(func.count(KnowledgeComponent.id)).join(KnowledgeParent)
                         .where(KnowledgeParent.source == "feedback")) or 0
    last = db.scalar(select(func.max(HistoricalBOQ.processed_at)).where(HistoricalBOQ.status.in_(STORED)))
    week = now() - timedelta(days=7)
    recent = db.scalar(select(func.count(KnowledgeComponent.id)).join(KnowledgeParent)
                       .where(KnowledgeParent.source == "historical", KnowledgeComponent.in_scope.is_(True),
                              KnowledgeParent.created_at >= week)) or 0
    scan = db.scalar(select(HistoricalScan).order_by(HistoricalScan.id.desc()))
    return {
        "files": {"total": sum(files.values()), "ingested": files.get("ingested", 0), "partial": files.get("partial", 0),
                  "indexed": files.get("ingested", 0) + files.get("partial", 0),
                  "failed": files.get("failed", 0), "duplicate": files.get("duplicate", 0),
                  "no_mappings": files.get("no_mappings", 0), "changed": files.get("changed", 0),
                  "processing": files.get("processing", 0), "queued": files.get("queued", 0),
                  "pending": files.get("queued", 0) + files.get("processing", 0)},
        "historical_parents": hist_parents,
        "highside_supply": by_type.get("Highside Supply", 0),
        "highside_installation": by_type.get("Highside Installation", 0),
        "historical_components": sum(by_type.values()),
        "feedback_parents": fb_parents, "feedback_components": fb_comps,
        "distinct_items": distinct, "items_reclassified_vs_master": reclassified,
        "last_ingested_at": last, "new_mappings_7d": recent,
        "latest_scan": None if scan is None else {
            "id": scan.id, "root_path": scan.root_path, "status": scan.status, "folders_scanned": scan.folders_scanned,
            "files_discovered": scan.files_discovered, "supported_files": scan.supported_files,
            "to_process": scan.to_process, "processed": scan.processed, "created_at": scan.created_at,
            "finished_at": scan.finished_at, "new_highside_mappings": (scan.summary or {}).get("new_highside_mappings")},
        "index": index_info(),
    }
