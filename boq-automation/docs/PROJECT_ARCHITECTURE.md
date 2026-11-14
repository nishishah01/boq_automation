# Project architecture

BOQ Component Mapping takes a client or Sales BOM BOQ, finds the Highside Supply and Highside
Installation components each BOQ item needs, lets an engineer review them, and writes them back
into **the same workbook, in its own format**, as yellow rows directly under their parent items.

Paths below are relative to `boq-automation/`.

## Components at a glance

```
 Browser (React + Vite, frontend/)
   │  /api/*  (axios, frontend/src/services/api.js)
   ▼
 FastAPI backend (backend/app/main.py, backend/app/api/routes.py)
   ├── Item Master            services/item_master/       read + normalise 18,932 components
   ├── BOQ parsing            services/boq_parser/        layout detection, hierarchy, eligibility
   ├── Matching engine        services/ai_matching/       requirement → retrieve → score → decide
   │      └── AI providers    claude / openai / gemini evaluators (shortlist only), or rules
   ├── Validation             services/validation/        Item Master is the only source of records
   ├── Knowledge base         services/knowledge/         historical BOQs + review feedback
   │      ├── extract.py      reads verified BOQs in any layout (read-only)
   │      ├── service.py      ingestion pipeline, folder scans, stats, snapshots
   │      └── index.py        vector + lexical search, technical compatibility, classification
   ├── Excel export           services/excel_export/      writes rows into a copy of the BOQ
   │      └── excel_processor/ooxml.py, shift.py           direct OOXML editing (macros kept)
   └── Jobs                   services/jobs.py            background runs, review, export
   ▼
 Database: SQLite (backend/data/boq.db) or PostgreSQL via DATABASE_URL
 Files:    backend/data/storage/uploads/{boq,item_master,historical}/<uuid>/<file>
           backend/data/storage/exports/<job>/...
```

## Frontend

React 18 + React Router + Tailwind, built with Vite (`frontend/`). One page per workflow step:
Upload → Run mapping → Review & approve → Export, plus Overview, **Knowledge base** and Settings.
`frontend/src/context/WorkContext.jsx` remembers the selected BOQ, Item Master and run;
`frontend/src/services/api.js` is the only place that calls the backend. In development Vite
proxies `/api` to the backend (`frontend/vite.config.js`, `VITE_API_PROXY`).

## Backend

FastAPI app in `backend/app/main.py`. On start it creates/extends the schema
(`database/session.py: ensure_schema`, additive only), marks interrupted mapping runs as failed,
and **requeues historical BOQs that were pending when the server stopped**
(`knowledge.service.resume_pending`). All routes are in `backend/app/api/routes.py`.

Long work runs off the request thread:

| Work | Executor | Where |
|---|---|---|
| Item Master ingestion, mapping runs | `jobs.executor` (2 threads) | `services/jobs.py` |
| Historical BOQ ingestion, folder scans, index rebuild | `knowledge.service.ingest_executor` (1 thread) | `services/knowledge/service.py` |

The historical worker is single-threaded on purpose: SQLite has one writer, and each file is its
own transaction, so one bad file never affects another.

## Database

SQLAlchemy 2 models in `backend/app/models/tables.py`; full description in
[DATABASE_SCHEMA.md](DATABASE_SCHEMA.md). The knowledge base lives in the same database as
everything else (`historical_scans`, `historical_boqs`, `knowledge_parents`,
`knowledge_components`) — there is no second store.

## AI integration

`services/ai_matching/providers.py` routes a **shortlist for one BOQ line** to Claude, OpenAI or
Gemini (`AI_MODE` / `AI_PROVIDER` in `backend/.env`). The model never sees the whole Item Master
and may only choose from the shortlist; its answer is schema-checked and re-validated against the
Item Master. Without a key, or after repeated provider failures, deterministic rule scoring
decides (`engine._decide_with_rules`). Historical knowledge is **not** sent to the model as files:
it is retrieved per line and passed as a few compact examples (`LineEvidence.payload`).
See [AI_MAPPING_LOGIC.md](AI_MAPPING_LOGIC.md).

## Embeddings and vector search

`services/ai_matching/retrieval.py: HybridTextIndex` combines word TF-IDF, character n-gram
TF-IDF and an embedding search over FAISS. The default embedder is LSA (TF-IDF + truncated SVD,
offline); `EMBEDDING_BACKEND=sentence-transformers` switches to a neural model. The same class
indexes the Item Master (per Cost Sheet Type) and the historical knowledge base
(`knowledge/index.py: _SubIndex`). Indexes are built in memory from database rows and rebuilt
when their source changes; nothing is retrained.

## Excel processing

* Reading: openpyxl, read-only, values only (`excel_processor/workbook.py`). Workbooks whose
  styles or named ranges openpyxl rejects are read from a repaired **in-memory** copy
  (`open_values_tolerant`); the file on disk is never written.
* Writing: the export edits the OOXML package directly (`excel_processor/ooxml.py`,
  `excel_processor/shift.py`) because openpyxl would drop macros, x14 validations and shapes.
  Rows are inserted the way Excel's *Insert Row* does: every formula, merged cell, conditional
  format, validation, filter and defined name below the insertion point moves down.
* Two writers share the same rules: `excel_export/exporter.py` (Sales BOM template) and
  `excel_export/generic.py` (any other layout). Both keep worksheet names, column order, styles,
  formulas and hierarchy, insert components directly below their parent's block and fill every
  inserted row yellow. `excel_export/verifier.py` proves this before a download is allowed.

## Data flow

```
Item Master .xlsx ──► item_master/processor.py ──► item_master_components ──► MasterIndexes (FAISS)
                                                                                   ▲  classification overrides
Historical BOQs (upload or folder scan) ──► knowledge/extract.py ──► knowledge_parents/components
                                                                     └► KnowledgeSnapshot (FAISS) ─┐
New BOQ .xlsx/.xlsm ──► boq_parser (layout, hierarchy, unit+qty rule) ──► MatchingEngine.match_line ◄┘
      ──► validation.finalize_line ──► component_mappings (review) ──► approvals → feedback knowledge
      ──► excel_export (same template, yellow rows under parents) ──► verifier ──► download
```

## Scope

Automated scope is configuration: `ENABLED_COST_SHEET_TYPES=Highside Supply,Highside Installation`
in `backend/.env`, checked everywhere through `core/cost_sheets.py: REGISTRY`. Lowside and other
types are never suggested; the knowledge base records them only as out-of-scope evidence.
