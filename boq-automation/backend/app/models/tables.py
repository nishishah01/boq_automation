from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    role: Mapped[str] = mapped_column(String(40), default="engineer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BOQUpload(Base):
    __tablename__ = "boq_uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    selected_sheet: Mapped[str] = mapped_column(String(120))
    sheets: Mapped[list] = mapped_column(JSON, default=list)
    has_macros: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ItemMasterUpload(Base):
    __tablename__ = "item_master_uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ItemMasterComponent(Base):
    __tablename__ = "item_master_components"
    __table_args__ = (UniqueConstraint("upload_id", "item_no"),
                      Index("ix_imc_upload_type", "upload_id", "cost_sheet_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("item_master_uploads.id", ondelete="CASCADE"))
    item_no: Mapped[str] = mapped_column(String(40))
    item_brief: Mapped[str] = mapped_column(Text)
    cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")
    uom: Mapped[str] = mapped_column(String(20), default="")
    cost: Mapped[float] = mapped_column(Float, default=0)
    family: Mapped[str] = mapped_column(String(120), default="")
    raw: Mapped[dict] = mapped_column(JSON)  # original record, every column


class ProcessingHistory(Base):
    """One AI processing run. Holds its configuration and live progress."""
    __tablename__ = "processing_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    boq_upload_id: Mapped[int] = mapped_column(ForeignKey("boq_uploads.id"))
    item_master_upload_id: Mapped[int] = mapped_column(ForeignKey("item_master_uploads.id"))
    sheet: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued running completed failed
    mode: Mapped[str] = mapped_column(String(20), default="mock")
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    boq: Mapped[BOQUpload] = relationship()
    item_master: Mapped[ItemMasterUpload] = relationship()


class BOQDescription(Base):
    __tablename__ = "boq_descriptions"
    __table_args__ = (Index("ix_desc_job", "job_id", "row_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_history.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    unique_no: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    context: Mapped[list] = mapped_column(JSON, default=list)
    cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")
    uom: Mapped[str] = mapped_column(String(20), default="")
    quantity: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20))  # matched partial unmatched skipped mapped pending
    skip_reason: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    requirements_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    unmatched: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    existing_children: Mapped[list] = mapped_column(JSON, default=list)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_note: Mapped[str] = mapped_column(Text, default="")
    engine: Mapped[str] = mapped_column(String(20), default="rule")
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    mappings: Mapped[list["ComponentMapping"]] = relationship(
        back_populates="description", cascade="all, delete-orphan", order_by="ComponentMapping.id")


class ComponentMapping(Base):
    __tablename__ = "component_mappings"
    __table_args__ = (UniqueConstraint("description_id", "item_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    description_id: Mapped[int] = mapped_column(ForeignKey("boq_descriptions.id", ondelete="CASCADE"))
    item_no: Mapped[str] = mapped_column(String(40))
    item_brief: Mapped[str] = mapped_column(Text)
    cost_sheet_type: Mapped[str] = mapped_column(String(60))
    class_name: Mapped[str] = mapped_column(String(20))
    cost: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[int] = mapped_column(Integer)
    selection_type: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    scope_label: Mapped[str] = mapped_column(Text, default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20), default="rule")  # rule claude openai gemini manual
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending review approved incorrect
    # Classification traceability. cost_sheet_type above is the final value written on export.
    master_cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")  # as in the Item Master
    ai_cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")  # what the engine recommended
    classification_source: Mapped[str] = mapped_column(String(20), default="item_master")  # historical feedback item_master manual
    classification_note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # historical/feedback examples supporting it
    description: Mapped[BOQDescription] = relationship(back_populates="mappings")


class MappingApproval(Base):
    """Append-only audit trail of every review action and correction."""
    __tablename__ = "mapping_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_history.id", ondelete="CASCADE"))
    description_id: Mapped[int] = mapped_column(ForeignKey("boq_descriptions.id", ondelete="CASCADE"))
    mapping_id: Mapped[int | None] = mapped_column(Integer)  # survives removal of the mapping
    action: Mapped[str] = mapped_column(String(30))
    item_no: Mapped[str | None] = mapped_column(String(40))
    previous_item_no: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExportRecord(Base):
    __tablename__ = "export_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_history.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20))  # verified failed
    boq_path: Mapped[str | None] = mapped_column(Text)
    mapping_report_path: Mapped[str | None] = mapped_column(Text)
    unmatched_report_path: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HistoricalScan(Base):
    """One scan of a folder of historical BOQs: the inventory it found and the progress of
    ingesting what was new or changed. The folder itself is only ever read."""
    __tablename__ = "historical_scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    root_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="scanning")  # scanning processing completed failed
    reprocess_changed: Mapped[bool] = mapped_column(Boolean, default=True)
    folders_scanned: Mapped[int] = mapped_column(Integer, default=0)
    files_discovered: Mapped[int] = mapped_column(Integer, default=0)  # every file, any type
    supported_files: Mapped[int] = mapped_column(Integer, default=0)  # .xlsx / .xlsm
    unsupported_files: Mapped[int] = mapped_column(Integer, default=0)
    temporary_files: Mapped[int] = mapped_column(Integer, default=0)  # ~$ lock files, ignored
    duplicate_files: Mapped[int] = mapped_column(Integer, default=0)  # byte-identical to another file
    unreadable_files: Mapped[int] = mapped_column(Integer, default=0)
    new_files: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_files: Mapped[int] = mapped_column(Integer, default=0)
    to_process: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    inventory: Mapped[list] = mapped_column(JSON, default=list)  # one entry per discovered file
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HistoricalBOQ(Base):
    """A previously completed, verified BOQ used as reference knowledge (never mapped itself).

    status: queued (Pending) | processing | ingested (Successfully Ingested) | partial
            (Partially Ingested) | no_mappings (readable, but no Highside mappings in it) |
            failed | duplicate | changed (source file changed; waiting to be reprocessed)
    """
    __tablename__ = "historical_boqs"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(Text)  # read-only copy the extraction works from
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)  # of the extracted mappings
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    parents_count: Mapped[int] = mapped_column(Integer, default=0)  # parent descriptions with Highside components
    components_count: Mapped[int] = mapped_column(Integer, default=0)  # Highside component mappings
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # Where it came from: "upload" (Knowledge base page) or "folder" (folder scan).
    origin: Mapped[str] = mapped_column(String(20), default="upload")
    source_path: Mapped[str] = mapped_column(Text, default="")  # original file, for folder ingestion
    relative_path: Mapped[str] = mapped_column(Text, default="")  # relative to the scanned root folder
    folder: Mapped[str] = mapped_column(Text, default="")  # project folder, relative to the root
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("historical_scans.id", ondelete="SET NULL"))
    source_mtime: Mapped[float] = mapped_column(Float, default=0)
    duplicate_of: Mapped[int | None] = mapped_column(Integer)
    highside_supply_count: Mapped[int] = mapped_column(Integer, default=0)
    highside_installation_count: Mapped[int] = mapped_column(Integer, default=0)
    out_of_scope_count: Mapped[int] = mapped_column(Integer, default=0)  # Lowside and other components seen
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeParent(Base):
    """One parent BOQ description with the components it was verified to need.

    source "historical": extracted from an uploaded verified BOQ (highest priority).
    source "feedback": approved in this app's review (second priority); one per reviewed line.
    """
    __tablename__ = "knowledge_parents"
    __table_args__ = (Index("ix_kp_source", "source", "historical_id"), Index("ix_kp_desc", "description_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    historical_id: Mapped[int | None] = mapped_column(ForeignKey("historical_boqs.id", ondelete="CASCADE"))
    description_id: Mapped[int | None] = mapped_column(Integer)  # feedback: the reviewed BOQ line
    job_id: Mapped[int | None] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300), default="")
    sheet: Mapped[str] = mapped_column(String(120), default="")
    row_number: Mapped[int] = mapped_column(Integer, default=0)
    unique_no: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(Text)
    context: Mapped[list] = mapped_column(JSON, default=list)
    cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")
    uom: Mapped[str] = mapped_column(String(20), default="")
    quantity: Mapped[float] = mapped_column(Float, default=0)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)  # extracted technical attributes
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # True when at least one component is Highside Supply / Installation (the enabled scope).
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    method: Mapped[str] = mapped_column(String(20), default="")  # how parent and components were linked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    components: Mapped[list["KnowledgeComponent"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", order_by="KnowledgeComponent.id")


class KnowledgeComponent(Base):
    __tablename__ = "knowledge_components"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("knowledge_parents.id", ondelete="CASCADE"), index=True)
    item_no: Mapped[str] = mapped_column(String(40), index=True)
    item_brief: Mapped[str] = mapped_column(Text, default="")
    cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")  # as used in the verified BOQ
    class_name: Mapped[str] = mapped_column(String(20), default="")
    master_cost_sheet_type: Mapped[str] = mapped_column(String(60), default="")  # Item Master value at ingest
    # The classification this mapping stands for: the BOQ's own value, or the Item Master's when
    # the engineer left it blank. type_source says which (historical | item_master | none).
    resolved_cost_sheet_type: Mapped[str] = mapped_column(String(60), default="", index=True)
    type_source: Mapped[str] = mapped_column(String(20), default="historical")
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    in_master: Mapped[bool] = mapped_column(Boolean, default=True)
    row_number: Mapped[int] = mapped_column(Integer, default=0)  # the component's own row in the source
    uom: Mapped[str] = mapped_column(String(20), default="")
    quantity: Mapped[float] = mapped_column(Float, default=0)
    parent: Mapped[KnowledgeParent] = relationship(back_populates="components")
