export interface SheetInfo { name: string; state: string; is_sales_bom: boolean; header_row: number | null }

export interface BoqUpload {
  id: number; filename: string; size_bytes: number; selected_sheet: string;
  sheets: SheetInfo[]; has_macros: boolean; created_at: string;
}

export interface ItemMasterUpload {
  id: number; filename: string; size_bytes: number; record_count: number;
  quality: {
    by_cost_sheet_type?: Record<string, number>; blank_cost_sheet_type?: number; duplicate_brief_groups?: number;
    conflicting_cost_sheet_groups?: number; deprecated?: number; zero_cost?: number;
    supply_items_worded_as_installation?: number;
  };
  status: "processing" | "ready" | "failed"; error: string | null; created_at: string;
}

export interface CostSheetOption { name: string; group: string; activity: string }

export interface Options {
  cost_sheet_types: CostSheetOption[]; ai_mode: string; model: string; embedding_backend: string; database: string;
  defaults: { confidence_threshold: number; max_candidates: number };
}

export interface Job {
  id: number; boq_upload_id: number; item_master_upload_id: number; sheet: string;
  config: { cost_sheet_types: string[]; confidence_threshold: number; max_candidates: number; suggest_inferred: boolean; manual_low_confidence: boolean };
  status: "queued" | "running" | "completed" | "failed"; mode: string; total: number; processed: number;
  summary: { counts?: Record<string, number>; warnings?: string[] }; error: string | null;
  created_at: string; finished_at: string | null;
}

export interface Mapping {
  id: number; item_no: string; item_brief: string; cost_sheet_type: string; class_name: string; cost: number;
  confidence: number; selection_type: "Explicit" | "Inferred"; reason: string; scope_label: string; flags: string[];
  source: "rule" | "claude" | "openai" | "gemini" | "manual"; status: "pending" | "review" | "approved" | "incorrect";
}

export interface Candidate {
  scope: string; activity: string; item_no: string; item_brief: string; cost_sheet_type: string;
  score: number; reasons: string[]; warnings: string[];
  decision?: "candidate" | "rejected"; exact_model?: boolean; conflicts?: string[];
}

export interface Description {
  id: number; job_id: number; row_number: number; unique_no: string; description: string; context: string[];
  cost_sheet_type: string; uom: string; quantity: number;
  status: "matched" | "partial" | "unmatched" | "skipped" | "mapped"; skip_reason: string;
  requirements: Record<string, unknown>; requirements_edited: boolean; candidates: Candidate[]; unmatched: string[];
  notes: string[]; existing_children: { item_no: string; item_brief: string; cost_sheet_type: string; unique_no: string }[];
  flagged: boolean; flag_note: string; engine: string; mappings: Mapping[];
}

export interface SearchItem { item_no: string; item_brief: string; cost_sheet_type: string; uom: string; cost: number; family: string }

export interface ExportRecord {
  id: number; job_id: number; status: "verified" | "failed";
  summary: Record<string, number>;
  verification: { ok?: boolean; errors?: string[]; checks?: Record<string, unknown> };
  created_at: string;
}

export interface Dashboard {
  boq_files: number; parent_descriptions: number; components_mapped: number; approved: number; pending: number;
  low_confidence: number;
  recent: { id: number; filename: string; status: string; mode: string; total: number; processed: number; created_at: string }[];
}
