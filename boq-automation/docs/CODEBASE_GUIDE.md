# Codebase guide

Where everything is, what it does and where to change it. Paths are relative to `boq-automation/`.

## Backend (`backend/app/`)

### Application and configuration

| File | Purpose | Main names | Change here to… |
|---|---|---|---|
| `main.py` | FastAPI app, CORS, start-up tasks (schema upgrade, fail interrupted runs, requeue historical files), readable 500s | `app`, `unexpected_error`, `health` | add start-up work or middleware |
| `core/config.py` | settings from `backend/.env` then environment | `Settings`, `get_settings`, `enabled_types`, `resolved_ai_mode` | add a setting (AI keys/models, `ENABLED_COST_SHEET_TYPES`, `EMBEDDING_BACKEND`, thresholds) |
| `core/cost_sheets.py` | registry of Cost Sheet Types (group + activity) | `REGISTRY`, `type_for` | add a new Cost Sheet Type; enabling it is `.env` only |
| `database/session.py` | engine, sessions, additive schema upgrade | `SessionLocal`, `get_db`, `ensure_schema` | database settings |
| `models/tables.py` | all tables | `HistoricalScan`, `HistoricalBOQ`, `KnowledgeParent`, `KnowledgeComponent`, `ItemMaster*`, `ProcessingHistory`, `BOQDescription`, `ComponentMapping`, … | add columns (picked up by `ensure_schema`) |
| `schemas/api.py` | Pydantic request/response models | `HistoricalOut`, `ScanIn`, `ScanOut`, `KnowledgeParentOut`, `JobCreate`, `MappingOut`, … | change what the API returns |
| `api/routes.py` | every HTTP endpoint ([API_REFERENCE.md](API_REFERENCE.md)) | uploads, layout, jobs, review, export, `historical*`, `knowledge*` | add or change endpoints |

### Historical knowledge base (`services/knowledge/`)

| File | Purpose | Main names | Interacts with |
|---|---|---|---|
| `extract.py` | reads parent → component mappings from a verified BOQ in any layout, read-only | `extract_workbook`, `_sales_bom` (imported / working / draft Sales BOM), `_generic`, `item_number_column`, `_flat_list`, `Extraction`, `ExtractedParent`, `ExtractedComponent` | `boq_parser.layout`, `boq_parser.parser.parse_generic`, `excel_processor.workbook.open_values_tolerant` |
| `service.py` | ingestion pipeline and storage; folder scans; retries; feedback; snapshots; stats | `ingest_historical`, `_ingest`, `_resolve`, `start_scan`, `run_scan`, `_inventory`, `retry_failed`, `resume_pending`, `submit`, `sync_feedback`, `snapshot`, `warm_index`, `stats` | `extract.py`, `index.py`, models, `routes.py`, `jobs.py` |
| `index.py` | searchable snapshot: vector/lexical search, technical compatibility, evidence, classification priority | `KnowledgeSnapshot.evidence`, `compatibility`, `Classifier.resolve/overrides`, `LineEvidence`, `ItemEvidence`, `SnapshotCache`, `parent_attrs` | `ai_matching.retrieval.HybridTextIndex`, `item_master.normalize`, `engine.py`, `validator.py` |

Change extraction rules in `extract.py` (`_Row.is_line`, `is_section`, `_flat_list`), the scope
and status rules in `service._resolve` / `service._ingest`, the similarity threshold and source
weights in `index.py` (`MIN_SIMILARITY`, `SOURCE_WEIGHT`, `CLEAR_MAJORITY`, `DOC_CHARS`,
`INDEX_DIMS`).

### BOQ parsing (`services/boq_parser/`)

| File | Purpose | Main names |
|---|---|---|
| `layout.py` | finds header row and column roles in any worksheet (synonyms, merged sub-headers, longest-text fallback) | `detect_layout`, `layout_at`, `validate_layout`, `ROLES` |
| `parser.py` | Sales BOM hierarchy (`parse_boq`) and generic BOQs (`parse_generic`: headings, lines, spec rows, existing component rows) | `BoqRow`, `SalesLine`, `ParsedBoq`, `parse_boq`, `parse_generic` |
| `eligibility.py` | unit + quantity rule for mapping a line | `unit_valid`, `quantity_state`, `check` |

### Matching (`services/ai_matching/`)

| File | Purpose | Main names |
|---|---|---|
| `engine.py` | the per-line pipeline ([AI_MAPPING_LOGIC.md](AI_MAPPING_LOGIC.md)) | `MatchingEngine.match_line`, `_shortlist`, `_companions`, `_check_scope`, `line_scope`, `MatchConfig`, `LineResult` |
| `requirements.py` | BOQ text → requirement with scope items and attributes | `build_requirement`, `split_clauses`, `precedent_key` |
| `retrieval.py` | hybrid TF-IDF + LSA/FAISS indexes (Item Master and knowledge base) | `HybridTextIndex`, `LsaEmbedder`, `ComponentIndex`, `MasterIndexes` |
| `scorer.py` | compatibility-first scoring | `score_candidate`, `history_strength`, `HISTORY_BONUS` |
| `providers.py`, `claude_evaluator.py`, `openai_evaluator.py`, `gemini_evaluator.py` | AI decision on a shortlist, schema-validated | `evaluate`, `ClaudeLineResult` |

### Item Master and validation

| File | Purpose | Main names |
|---|---|---|
| `services/item_master/processor.py` | reads the master workbook into `Component`s; data-quality report | `read_item_master`, `Component`, `reclassified`, `data_quality` |
| `services/item_master/normalize.py` | shared text normalisation and attribute parsing | `canon`, `extract_attrs`, `Attrs` (`dump`/`load`), `forms_conflict` |
| `services/validation/validator.py` | Item Master is the only source of records; classification priority applied | `classify`, `finalize_line`, `validate_for_export`, `component_flags` |

### Jobs (`services/jobs.py`)

Item Master ingestion (`ingest_item_master`, which also requeues historical files read without a
master), mapping runs (`run_job`, `regenerate`), building Item Master indexes with historical
classification overrides (`indexes_for`), creating mappings (`mapping_from_selection`,
`mapping_from_master`, `initial_status`), export orchestration (`run_export`) and file storage
(`store_upload`).

### Excel (`services/excel_processor/`, `services/excel_export/`)

| File | Purpose | Main names |
|---|---|---|
| `excel_processor/workbook.py` | read-only inspection; tolerant opening of damaged workbooks | `open_values`, `open_values_tolerant`, `inspect_boq`, `preview_sheet`, `find_header` |
| `excel_processor/ooxml.py` | direct package editing (keeps macros, validations, shapes) | `XlsxPackage`, `SheetDoc`, `SharedStrings`, `StyleSheet.filled/fill_dxf`, `add_override_format` |
| `excel_processor/shift.py` | Excel-style row insertion for generic BOQs (formulas, merges, CF, validations, names, tables) | `insert_rows`, `shift_formula` |
| `excel_export/exporter.py` | Sales BOM export: child rows under each sales line, yellow | `export_boq`, `child_values`, `ChildPlan`, `INSERTED_FILL` |
| `excel_export/generic.py` | any other layout: rows under each BOQ item block, yellow, sheet's own columns used | `export_generic` |
| `excel_export/verifier.py` | blocks a download unless the export is provably correct | `verify_export`, `_verify_generic`, `_verify_placement` |
| `excel_export/reports.py` | mapping and unmatched report workbooks | `mapping_report`, `unmatched_report` |

`_verify_placement` checks, per parent: new rows directly below the parent's block, consecutive,
in the original parent order, nothing inserted inside the block, whatever followed still follows;
every inserted cell solid yellow; parent and pre-existing rows keep their fill.

### Tests and scripts (`backend/tests`, `backend/scripts`)

`tests/test_historical_ingestion.py` (Sales BOM variants, flat lists, damaged styles, folder
scans, duplicates, change detection, retry), `tests/test_knowledge.py` (priority, compatibility,
conflicts, feedback), `tests/test_export.py` (export, placement/yellow verification),
`tests/test_generic_boq.py`, `tests/test_api_flow.py`, `tests/test_matching*.py`. Run
`cd backend && .venv/Scripts/python -m pytest -q`. `scripts/evaluate_real.py` measures accuracy
against real engineer-mapped BOQs.

## Frontend (`frontend/src/`)

| File | Purpose | API calls |
|---|---|---|
| `main.jsx` | routes | — |
| `layouts/AppLayout.jsx` | sidebar with workflow steps and workspace links | `progress` (via context) |
| `context/WorkContext.jsx` | selected BOQ / Item Master / run, remembered locally | `options`, `progress` |
| `services/api.js` | every backend call (axios) | all |
| `pages/UploadPage.jsx` + `components/ColumnMapping.jsx` | upload BOQ and Item Master; confirm columns of non-template BOQs | `uploadBoq`, `boqLayout`, `saveBoqLayout`, `uploadItemMaster` |
| `pages/MappingPage.jsx` | start and follow a run | `createJob`, `job`, `descriptions` |
| `pages/ReviewPage.jsx` | review lines and components, evidence, reclassify, replace, add | `approve`, `reclassify`, `replace`, `addMapping`, … |
| `pages/ExportPage.jsx` | export, verification result, downloads | `exportJob`, `exports`, `downloadUrl` |
| `pages/KnowledgePage.jsx` | **Knowledge base**: metrics, bulk folder ingestion (`FolderScan`), individual upload, inventory drawer (`Inventory`), file table with status filters and actions, file drawer (`Mappings`), mapping search (`MappingSearch`), "what would it suggest" (`TryIt`) | `historical`, `uploadHistorical`, `startScan`, `scan`, `knowledgeStats`, `historicalMappings`, `knowledgeMappings`, `knowledgeSearch`, `retryFailedHistorical`, `reprocessHistorical`, `reprocessAllHistorical`, `replaceHistorical`, `deleteHistorical` |
| `pages/DashboardPage.jsx` | overview incl. knowledge totals | `dashboard` |
| `pages/SettingsPage.jsx` | read-only configuration | `options` |
| `components/ui.jsx` | shared UI (`MetricStrip`, `Status` labels incl. knowledge statuses, `Drawer`, `Segmented`, `FileDrop`, `Progress`, `InfoTip`) | — |
| `components/feedback.jsx` | toasts and confirm dialogs | — |
| `utils/status.js` | line/component status helpers for review | — |

To add a status label, edit `STATUS` in `components/ui.jsx`. To change the Knowledge page layout,
edit `pages/KnowledgePage.jsx`; its data comes only from `services/api.js`.
