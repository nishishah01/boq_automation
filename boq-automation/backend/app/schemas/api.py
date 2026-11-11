from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BOQUploadOut(ORM):
    id: int
    filename: str
    size_bytes: int
    selected_sheet: str
    sheets: list
    has_macros: bool
    created_at: datetime


class ItemMasterUploadOut(ORM):
    id: int
    filename: str
    size_bytes: int
    record_count: int
    quality: dict
    status: str
    error: str | None
    created_at: datetime


class JobCreate(BaseModel):
    boq_upload_id: int
    item_master_upload_id: int
    sheet: str | None = None
    cost_sheet_types: list[str] = Field(min_length=1)
    confidence_threshold: int = Field(default=75, ge=0, le=100)
    max_candidates: int = Field(default=5, ge=1, le=20)
    suggest_inferred: bool = True
    manual_low_confidence: bool = True
    reprocess_mapped: bool = False


class JobOut(ORM):
    id: int
    boq_upload_id: int
    item_master_upload_id: int
    sheet: str
    config: dict
    status: str
    mode: str
    total: int
    processed: int
    summary: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class MappingOut(ORM):
    id: int
    item_no: str
    item_brief: str
    cost_sheet_type: str
    class_name: str
    cost: float
    confidence: int
    selection_type: str
    reason: str
    scope_label: str
    flags: list
    source: str
    status: str


class DescriptionOut(ORM):
    id: int
    job_id: int
    row_number: int
    unique_no: str
    description: str
    context: list
    cost_sheet_type: str
    uom: str
    quantity: float
    status: str
    skip_reason: str
    requirements: dict
    requirements_edited: bool
    candidates: list
    unmatched: list
    notes: list
    existing_children: list
    flagged: bool
    flag_note: str
    engine: str
    mappings: list[MappingOut]


class AddMapping(BaseModel):
    item_no: str


class ReplaceMapping(BaseModel):
    item_no: str
    note: str = ""


class FlagRequest(BaseModel):
    note: str = ""
    flagged: bool = True


class RequirementsEdit(BaseModel):
    specification: str = ""
    engineer_note: str = ""


class MarkIncorrect(BaseModel):
    note: str = ""


class ExportOut(ORM):
    id: int
    job_id: int
    status: str
    summary: dict
    verification: dict
    created_at: datetime
