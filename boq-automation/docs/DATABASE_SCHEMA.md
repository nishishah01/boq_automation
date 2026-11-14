# Database schema

Models: `backend/app/models/tables.py` (SQLAlchemy 2). Engine/session: `backend/app/database/session.py`.
SQLite by default (`backend/data/boq.db`, WAL mode, foreign keys on); PostgreSQL when
`DATABASE_URL` is set. `ensure_schema()` creates missing tables on start-up and **adds** columns
that newer versions introduce (it never drops or alters existing ones), so an existing database
upgrades in place.

## Relationships

```
users ─┬─< boq_uploads ──< processing_history >── item_master_uploads ──< item_master_components
       │                         │
       │                         ├──< boq_descriptions ──< component_mappings
       │                         ├──< mapping_approvals
       │                         └──< export_records
       │
       └─< historical_boqs >── historical_scans
                 │
                 └──< knowledge_parents ──< knowledge_components
                         (source = historical)

   knowledge_parents (source = feedback) ── description_id → boq_descriptions.id (logical link)
```

`──<` = one-to-many. Deleting a `historical_boqs` row cascades to its knowledge parents and their
components; deleting a scan sets `historical_boqs.scan_id` to NULL.

## Knowledge-base tables

### `historical_scans` — one folder scan (new)

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| root_path | TEXT | absolute folder that was scanned |
| status | VARCHAR(20) | `scanning` → `processing` → `completed` / `failed` |
| reprocess_changed | BOOLEAN | reprocess changed files (true) or only flag them (false) |
| folders_scanned, files_discovered | INTEGER | every folder / every file of any type |
| supported_files, unsupported_files, temporary_files | INTEGER | `.xlsx/.xlsm`; other types; `~$` lock files |
| duplicate_files, unreadable_files | INTEGER | byte-identical copies; files that could not be opened |
| new_files, changed_files, unchanged_files | INTEGER | compared with what earlier scans ingested |
| to_process, processed | INTEGER | progress of the files this scan queued |
| inventory | JSON | one entry per file: `relative_path, folder, filename, extension, size_bytes, status, reason, change, historical_id` |
| summary | JSON | `processed_by_status`, `new_highside_mappings`, `index` (build state) |
| error | TEXT NULL | why a scan failed |
| created_at, finished_at | DATETIME | |

### `historical_boqs` — one historical workbook (extended)

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| filename | VARCHAR(300) | |
| stored_path | TEXT | the application's own copy, read by extraction |
| size_bytes | INTEGER | |
| sha256 | VARCHAR(64) | indexed; byte-level duplicate and change detection |
| fingerprint | VARCHAR(64) | indexed; hash of the extracted mappings (mapping-level duplicates) |
| status | VARCHAR(20) | `queued, processing, ingested, partial, no_mappings, failed, duplicate, changed` |
| error | TEXT NULL | failure reason, partial reasons, or why nothing was learned |
| verified | BOOLEAN | historical BOQs are verified (true) |
| parents_count | INTEGER | parent descriptions with at least one Highside component |
| components_count | INTEGER | Highside Supply + Highside Installation mappings |
| highside_supply_count, highside_installation_count | INTEGER | **new** |
| out_of_scope_count | INTEGER | **new** – Lowside / other components seen (not learned as Highside) |
| summary | JSON | `sheets` (per worksheet: format, parents, components, method, note), `warnings`, `item_master`, `repaired`, `orphan_rows`, `partial_reasons`, `notes`, `by_cost_sheet_type`, `classification_differs_from_master`, `classified_from_item_master`, `not_in_item_master`, `unique_item_numbers`, `extracted_at` |
| origin | VARCHAR(20) | **new** – `upload` or `folder` |
| source_path | TEXT | **new** – original file path (folder ingestion) |
| relative_path, folder | TEXT | **new** – relative to the scanned root; project folder |
| scan_id | INTEGER NULL | **new** – FK `historical_scans.id` |
| source_mtime | FLOAT | **new** |
| duplicate_of | INTEGER NULL | **new** – the file this one duplicates |
| attempts | INTEGER | **new** – processing attempts (retries) |
| uploaded_by | INTEGER NULL | FK `users.id` |
| created_at, processed_at | DATETIME | `processed_at` = extraction date |

### `knowledge_parents` — one parent BOQ description with its verified components (extended)

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| source | VARCHAR(20) | `historical` (priority 1) or `feedback` (priority 2) |
| historical_id | INTEGER NULL | FK `historical_boqs.id`, ON DELETE CASCADE |
| description_id, job_id | INTEGER NULL | feedback: the reviewed BOQ line and run |
| filename, sheet | VARCHAR | source worksheet |
| row_number | INTEGER | the parent's row in the source worksheet |
| unique_no | VARCHAR(40) | Unique No / Cust Ref No. / serial |
| description | TEXT | the parent BOQ description |
| context | JSON | ancestor headings, nearest first |
| cost_sheet_type, uom, quantity | | as written on the parent row |
| attrs | JSON | technical attributes (models, capacity, sizes, materials, form, side, refrigerant, brand); `_full` is the lossless copy the index loads |
| verified | BOOLEAN | |
| in_scope | BOOLEAN | **new**, indexed – has at least one Highside component |
| method | VARCHAR(20) | **new** – `hierarchy`, `row order`, `generic`, `review` |
| created_at | DATETIME | |

Indexes: `ix_kp_source (source, historical_id)`, `ix_kp_desc (description_id)`, `ix_knowledge_parents_in_scope`.

### `knowledge_components` — one component of a parent (extended)

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | PK |
| parent_id | INTEGER | FK `knowledge_parents.id`, ON DELETE CASCADE, indexed |
| item_no | VARCHAR(40) | indexed; spelled as in the Item Master when known |
| item_brief | TEXT | |
| cost_sheet_type | VARCHAR(60) | **as written in the verified BOQ** ("" if left blank) |
| class_name | VARCHAR(20) | Component / Installation |
| master_cost_sheet_type | VARCHAR(60) | the Item Master's classification at ingestion (kept separately, never overwritten) |
| resolved_cost_sheet_type | VARCHAR(60) | **new**, indexed – the classification this mapping stands for |
| type_source | VARCHAR(20) | **new** – `historical`, `item_master`, `feedback`, `none` |
| in_scope | BOOLEAN | **new** – resolved type is Highside Supply / Installation |
| in_master | BOOLEAN | Item Number exists in the current Item Master |
| row_number | INTEGER | **new** – the component's own row in the source |
| uom, quantity | | **new** |

## Mapping tables (unchanged)

| Table | Purpose |
|---|---|
| `users` | one local user in v1 (`name` unique) |
| `boq_uploads` | uploaded BOQs to automate; `sheets` JSON holds each worksheet's detected/confirmed layout |
| `item_master_uploads` / `item_master_components` | Item Master versions; unique `(upload_id, item_no)`, index `(upload_id, cost_sheet_type)`; `raw` keeps every original column |
| `processing_history` | one mapping run: config, progress, summary (incl. knowledge used) |
| `boq_descriptions` | one BOQ line of a run: requirement, candidates, notes, `requirements.historical_examples` |
| `component_mappings` | one suggested component: final `cost_sheet_type`, `master_cost_sheet_type`, `ai_cost_sheet_type`, `classification_source` (historical/feedback/item_master/manual), `classification_note`, `evidence` (source BOQ references); unique `(description_id, item_no)` |
| `mapping_approvals` | append-only audit trail of review actions |
| `export_records` | export files and the verifier's result (`verification.checks.placement_verified`, `inserted_rows_yellow` …) |

## Vector indexes

Vector indexes are not tables: they are FAISS indexes built in memory from these rows.

| Index | Built from | Code | Rebuilt when |
|---|---|---|---|
| Item Master, one per Cost Sheet Type | `item_master_components` (+ historical classification overrides) | `ai_matching/retrieval.py: MasterIndexes` | a new master is used, or a verified classification changes |
| Historical knowledge | `knowledge_parents` (source historical, status ingested/partial) | `knowledge/index.py: _SubIndex` | any historical file is ingested, reprocessed or removed |
| Review feedback | `knowledge_parents` (source feedback) | `knowledge/index.py: _SubIndex` | any review action |
