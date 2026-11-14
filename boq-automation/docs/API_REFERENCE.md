# API reference

Base URL: `http://<host>:8000/api` (the frontend calls `/api` through the Vite proxy). All routes
are in `backend/app/api/routes.py`; response models in `backend/app/schemas/api.py`. Errors are
JSON `{"detail": "..."}` with 4xx codes; unexpected errors are 500 with a readable reason
(`backend/app/main.py: unexpected_error`).

## Historical BOQ knowledge base

### `POST /historical` — upload one historical BOQ
Multipart `file` (.xlsx/.xlsm). Stores a copy and queues it; processing starts automatically.
* 200 → `HistoricalOut` (status `queued`)
* 409 → the same bytes are already in the knowledge base
* 400 → not an Excel workbook

### `GET /historical` — list files
Query: `q` (filename or relative path contains), `status` (`ingested`, `partial`, `no_mappings`,
`failed`, `duplicate`, `changed`, `pending` = queued+processing, `stored` = ingested+partial),
`scan_id`, `limit` (≤5000, default 1000). → `HistoricalOut[]`

`HistoricalOut`: `id, filename, size_bytes, status, error, verified, parents_count,
components_count, highside_supply_count, highside_installation_count, out_of_scope_count, summary,
origin, source_path, relative_path, folder, scan_id, duplicate_of, attempts, sha256, created_at,
processed_at`.

### `GET /historical/{id}` → `HistoricalOut`

### `GET /historical/{id}/mappings` — what was extracted from one file
Query: `q` (description contains), `scope` (`highside` default | `all`), `limit` (≤500), `offset`.
→ `{"total": n, "items": KnowledgeParentOut[]}`

`KnowledgeParentOut`: `id, historical_id, filename, sheet, row_number, unique_no, description,
context, uom, quantity, cost_sheet_type, in_scope, method, created_at, components[]`;
component: `item_no, item_brief, cost_sheet_type, class_name, master_cost_sheet_type,
resolved_cost_sheet_type, type_source, in_scope, in_master, row_number, uom, quantity`.

### `POST /historical/{id}/reprocess`
Re-extracts one file (also applies a change a scan flagged). Previous mappings are replaced, not
duplicated. 409 while busy, for a duplicate (reprocess the original), or for a file that was never
readable (use retry-failed).

### `POST /historical/reprocess-all` → `{"queued": n}`
### `POST /historical/retry-failed` → `{"queued": n}`
Re-reads failed folder files from their original path (if readable now) and requeues them.

### `PUT /historical/{id}/file` — replace with a corrected version (multipart `file`)
### `DELETE /historical/{id}`
Removes the file's knowledge and the application's own copy. Originals in scanned folders are never
touched.

### `POST /historical/scans` — scan a folder
Body `ScanIn`: `{"root_path": "C:/…/Kytes Projects", "reprocess_changed": true}`.
Scans every subfolder; new files are ingested, unchanged ones skipped, changed ones reprocessed
(or flagged when `reprocess_changed` is false).
* 200 → `ScanOut` (status `scanning`)
* 400 → folder not found / not accessible from the server
* 409 → another scan is running

`ScanOut`: `id, root_path, status, reprocess_changed, folders_scanned, files_discovered,
supported_files, unsupported_files, temporary_files, duplicate_files, unreadable_files, new_files,
changed_files, unchanged_files, to_process, processed, summary, error, created_at, finished_at`.

### `GET /historical/scans` → `ScanOut[]` (latest 50)
### `GET /historical/scans/{id}` — scan with its complete inventory
Query `status` filters inventory entries. → `ScanOut` + `inventory: [{relative_path, folder,
filename, extension, size_bytes, status, reason, change, historical_id}]` (statuses are live).

### `GET /knowledge/stats`
```json
{
  "files": {"total", "ingested", "partial", "indexed", "failed", "duplicate", "no_mappings",
            "changed", "processing", "queued", "pending"},
  "historical_parents": 0, "highside_supply": 0, "highside_installation": 0,
  "historical_components": 0, "feedback_parents": 0, "feedback_components": 0,
  "distinct_items": 0, "items_reclassified_vs_master": 0,
  "last_ingested_at": "…", "new_mappings_7d": 0,
  "latest_scan": {"id", "root_path", "status", "folders_scanned", "files_discovered", "…"},
  "index": {"built_at", "seconds", "historical_parents", "feedback_parents", "version",
            "current_version", "up_to_date", "embedding_backend"}
}
```
`indexed` = ingested + partial (kept for the Overview page).

### `GET /knowledge/mappings` — search all stored Highside mappings
Query: `q` (all words in the description), `item_no` (contains), `cost_sheet_type`
(`Highside Supply` | `Highside Installation`), `folder` (relative path contains), `limit` (≤200),
`offset`. → `{"total", "items": [KnowledgeParentOut + "source": {historical_id, filename,
relative_path, source_path, folder, status}]}`

### `GET /knowledge/parents/{id}` — one mapping with full traceability
→ `KnowledgeParentOut` + `attrs` + `source: {historical_id, filename, source_path, relative_path,
folder, status, sha256, extracted_at, processed_at}`

### `GET /knowledge/search?q=…&limit=10` — what the engine would use for a description
Runs the same retrieval as a mapping run: similar verified descriptions **with compatible technical
attributes**. → `{"items": [{source, file, sheet, row, description, similarity, parent_id, copies,
agrees_on, components: [{item_no, item_brief, cost_sheet_type}]}]}`

### `POST /knowledge/index/rebuild` — build the vector index now → index state

## Mapping workflow (unchanged, for reference)

| Method & URL | Purpose |
|---|---|
| `GET /config/options` | enabled Cost Sheet Types, AI mode, embedding backend, defaults |
| `POST /uploads/boq`, `GET /uploads/boq` | upload / list BOQs to automate |
| `GET /uploads/boq/{id}/layout?sheet=&header_row=` | detected columns of a worksheet |
| `PUT /uploads/boq/{id}/layout` | confirm worksheet, header row and columns |
| `GET /uploads/boq/{id}/preview` | first rows of a worksheet |
| `POST /uploads/item-master`, `GET /uploads/item-master` | Item Master upload (background) / list |
| `GET /item-master/{id}/search`, `GET /item-master/{id}/items/{item_no}` | browse the master |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}` | start / list / poll a mapping run |
| `GET /jobs/{id}/descriptions`, `GET /jobs/{id}/audit` | results and audit trail |
| `POST /mappings/{id}/approve` · `/incorrect` · `/replace` · `/cost-sheet-type`, `DELETE /mappings/{id}` | review actions (each updates feedback knowledge) |
| `POST /descriptions/{id}/mappings` · `/approve-all` · `/regenerate` · `/flag`, `PATCH /descriptions/{id}/requirements` | line-level review |
| `POST /jobs/{id}/export`, `GET /jobs/{id}/exports`, `GET /exports`, `GET /exports/{id}/download/{boq|mapping|unmatched}` | export, verification, download |
| `GET /progress`, `GET /dashboard` | sidebar progress and overview |
