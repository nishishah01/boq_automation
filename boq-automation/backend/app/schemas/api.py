from typing import Literal
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
    # A quantity of 0: "review" maps the line and flags every suggestion; "skip" leaves it unmapped.
    zero_quantity: Literal["review", "skip"] = "review"


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
    master_cost_sheet_type: str | None = ""
    ai_cost_sheet_type: str | None = ""
    classification_source: str | None = "item_master"
    classification_note: str | None = ""
    evidence: list | None = []


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


class Reclassify(BaseModel):
    cost_sheet_type: str
    note: str = ""


class HistoricalOut(ORM):
    id: int
    filename: str
    size_bytes: int
    status: str
    error: str | None
    verified: bool
    parents_count: int
    components_count: int
    highside_supply_count: int = 0
    highside_installation_count: int = 0
    out_of_scope_count: int = 0
    summary: dict
    origin: str = "upload"
    source_path: str = ""
    relative_path: str = ""
    folder: str = ""
    scan_id: int | None = None
    duplicate_of: int | None = None
    attempts: int = 0
    sha256: str = ""
    created_at: datetime
    processed_at: datetime | None


class ScanIn(BaseModel):
    root_path: str
    reprocess_changed: bool = True


class ScanOut(ORM):
    id: int
    root_path: str
    status: str
    reprocess_changed: bool
    folders_scanned: int
    files_discovered: int
    supported_files: int
    unsupported_files: int
    temporary_files: int
    duplicate_files: int
    unreadable_files: int
    new_files: int
    changed_files: int
    unchanged_files: int
    to_process: int
    processed: int
    summary: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class KnowledgeComponentOut(ORM):
    item_no: str
    item_brief: str
    cost_sheet_type: str
    class_name: str
    master_cost_sheet_type: str
    resolved_cost_sheet_type: str = ""
    type_source: str = ""
    in_scope: bool = True
    in_master: bool
    row_number: int = 0
    uom: str = ""
    quantity: float = 0


class KnowledgeParentOut(ORM):
    id: int
    historical_id: int | None = None
    filename: str = ""
    sheet: str
    row_number: int
    unique_no: str
    description: str
    context: list
    uom: str
    quantity: float
    cost_sheet_type: str = ""
    in_scope: bool = True
    method: str = ""
    created_at: datetime | None = None
    components: list[KnowledgeComponentOut]


class ExportOut(ORM):
    id: int
    job_id: int
    status: str
    summary: dict
    verification: dict
    created_at: datetime
