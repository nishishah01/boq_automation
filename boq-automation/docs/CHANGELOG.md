# Changelog

## 2026-11-13 — Bulk historical BOQ ingestion, Highside-scoped knowledge, export verification

### Features
* **Folder ingestion**: scan a root folder and every subfolder (`POST /api/historical/scans`),
  complete file inventory with per-file status and reason, SHA-256 change detection, byte-level
  duplicates, unchanged files skipped on rescan, changed files reprocessed in place (or flagged).
* **Automatic processing** of uploads and scanned files on one background worker; the vector index
  rebuilds automatically when the queue empties; pending files resume after a server restart.
* **Format handling**: Sales BOM imported, working (blank Unique No → row order + Cust Ref No.) and
  draft (blank Sales Item) variants; generic layouts as before; flat component lists rejected with
  a reason; workbooks with malformed styles/defined names read from a repaired in-memory copy.
* **Highside scope**: only Highside Supply / Highside Installation become knowledge; other
  components are stored as out-of-scope evidence (never suggested, used to stop Lowside items
  being promoted and to flag Lowside-looking lines). Historical type kept separate from the Item
  Master type; blank historical types fall back to the Item Master and are labelled as such.
* **Statuses**: `queued, processing, ingested, partial, no_mappings, failed, duplicate, changed`
  with reasons; partial only when Highside information could not be learned.
* **Traceability**: original path, relative path, project folder, scan, SHA-256, extraction date,
  parent row and every component row stored and returned by the API.
* **Retry / reprocess**: `POST /api/historical/retry-failed` (re-reads originals),
  per-file reprocess, reprocess-all; reprocessing never duplicates mappings.
* **Search**: `GET /api/knowledge/mappings` (description words, Item No., type, folder) and
  `GET /api/knowledge/parents/{id}` (full source detail); stats extended.
* **Knowledge base page**: two metric strips (folders, files, ingested, processing, partial,
  failed; descriptions, HS Supply, HS Installation, Item Nos., new, last ingestion), bulk folder
  ingestion with progress, inventory drawer, status filters, retry/reprocess actions, source
  details per file, mapping search.
* **Export verification** (mandatory conditions): every inserted component is directly below its
  parent's block, consecutive, in parent order, nothing inserted inside a block, every inserted row
  solid yellow across the row, parent/pre-existing rows keep their fill
  (`verifier._verify_placement`). A failure blocks the download.
* **Performance**: knowledge index rebuild for ~6,000 parents went from ~130 s to ~15–25 s
  (stored attributes reused, cached normalised text, 128-dim LSA for the knowledge index).

### Files created
* `backend/tests/test_historical_ingestion.py`
* `docs/PROJECT_ARCHITECTURE.md`, `docs/CODEBASE_GUIDE.md`, `docs/AI_MAPPING_LOGIC.md`,
  `docs/HISTORICAL_KNOWLEDGE_BASE.md`, `docs/DATABASE_SCHEMA.md`, `docs/API_REFERENCE.md`,
  `docs/INGESTION_GUIDE.md`, `docs/CHANGELOG.md`

### Files modified
* `backend/app/models/tables.py` — `HistoricalScan`; new columns on `HistoricalBOQ`,
  `KnowledgeParent`, `KnowledgeComponent`
* `backend/app/schemas/api.py` — `HistoricalOut` extended, `ScanIn`, `ScanOut`, knowledge outputs extended
* `backend/app/api/routes.py` — historical/knowledge endpoints rewritten and extended
* `backend/app/main.py` — resume pending historical files on start-up
* `backend/app/services/knowledge/extract.py` — rewritten (Sales BOM variants, flat lists, orphans, rows)
* `backend/app/services/knowledge/service.py` — rewritten (pipeline, scans, scope, statuses, stats, index)
* `backend/app/services/knowledge/index.py` — `copies`, `resolved_type`/`scope_type`, cached doc, index size
* `backend/app/services/ai_matching/engine.py` — scope check uses resolved types
* `backend/app/services/ai_matching/retrieval.py` — `HybridTextIndex(dims=…)`
* `backend/app/services/item_master/normalize.py` — `Attrs.dump/load`
* `backend/app/services/excel_processor/workbook.py` — `open_values_tolerant`, TypeError handling
* `backend/app/services/excel_export/exporter.py`, `generic.py` — block info for verification
* `backend/app/services/excel_export/verifier.py` — `_verify_placement`
* `backend/app/services/jobs.py` — historical requeue goes through the ingestion worker
* `backend/tests/test_knowledge.py`, `backend/tests/test_export.py` — new statuses, placement tests
* `frontend/src/pages/KnowledgePage.jsx` — rebuilt dashboard
* `frontend/src/services/api.js` — scan, retry, search, index endpoints
* `frontend/src/components/ui.jsx` — knowledge status labels; hidden tooltips no longer widen pages
* `.claude/launch.json` — `api` (port 8001) and `web` (port 5174) launch entries

### Files removed
None.

### Dependencies
None added or changed.

### Database changes (applied automatically by `ensure_schema`, additive only)
* New table `historical_scans`.
* `historical_boqs`: `origin, source_path, relative_path, folder, scan_id, source_mtime,
  duplicate_of, highside_supply_count, highside_installation_count, out_of_scope_count, attempts`.
* `knowledge_parents`: `in_scope` (indexed), `method`; `attrs._full` stored for new extractions.
* `knowledge_components`: `resolved_cost_sheet_type` (indexed), `type_source`, `in_scope`,
  `row_number`, `uom`, `quantity`.
* Status value `indexed` replaced by `ingested` / `partial` (`stats.files.indexed` remains as their sum).

### Bugs fixed
* Working-copy and draft Sales BOMs (blank Unique No / Sales Item) produced no knowledge.
* A flat ERP component list was stored as one parent owning 62 unrelated components.
* Workbooks with malformed stylesheets or defined names could not be read at all.
* Lowside components were indexed with the same weight as Highside knowledge.
* Hidden InfoTip tooltips caused a horizontal page scroll.

### Initial ingestion of `Kytes Projects` (scan #1, 2026-11-13)
* 35 folders, 138 files: 100 `.xlsx/.xlsm`, 38 unsupported (26 `.msg`, 12 `.xls`), 0 lock files,
  0 byte-identical duplicates, 1 file locked during the scan (ingested on retry).
* 32 files in the knowledge base (31 ingested, 1 partial), 3 mapping duplicates, 65 without
  Highside mappings (57 client/OC/Job Code/summary sheets with no Item Numbers, 7 Lowside-only
  Sales BOMs, 1 flat component list), 0 failed.
* 654 parent descriptions with 373 Highside Supply and 383 Highside Installation mappings,
  440 unique Item Numbers; 9,994 out-of-scope components kept as evidence; 5 workbooks read from a
  repaired copy.

### Known limitations
* 375 Highside mappings (214 Item Numbers, almost all `IN-` installation codes) are not in the
  loaded Item Master, which contains only `CO-` codes; they are stored but cannot be suggested
  until an Item Master containing the `IN-` codes is loaded.
* `.xls` files must be saved as `.xlsx` to be ingested.
* Folder scans read paths on the server machine; a browser cannot pick a server folder, so the
  path is typed.
