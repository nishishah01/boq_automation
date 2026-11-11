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
