# Historical BOQ knowledge base

Completed, verified BOQs are the highest-priority source of truth for which Highside components a
BOQ item needs and how they are classified. This document explains how they get into the
knowledge base and how the matching engine uses them. Paths are relative to `boq-automation/`.

## 1. Two ways in

| | Individual upload | Folder scan |
|---|---|---|
| UI | Knowledge base page → *Individual upload* | Knowledge base page → *Bulk folder ingestion* |
| API | `POST /api/historical` | `POST /api/historical/scans` |
| Code | `routes.upload_historical` | `knowledge.service.start_scan` → `run_scan` → `_inventory` |
| Starts processing | immediately, automatically | immediately, automatically |

Both end in the same function, `knowledge.service.ingest_historical(hid)`, on the same
single-threaded worker (`ingest_executor`). There is no separate manual step: when the last queued
file finishes, the search index is rebuilt in the background (`_ingest_and_index` → `warm_index`).

## 2. Nested folder scanning (`service._inventory`)

```
os.walk(root)  (every depth, folders sorted)
  ├── "~$..."            → ignored   (Excel lock file)
  ├── not .xlsx/.xlsm    → unsupported, with the reason (.xls, .msg, .pdf …)
  ├── cannot be opened   → failed ("may be open in Excel or syncing from OneDrive"), retryable
  ├── same path, same SHA-256 as before → unchanged, skipped
  ├── same SHA-256 as another file      → duplicate (points to the first), not processed
  ├── same path, different SHA-256      → changed: new copy, requeued (or flagged "changed")
  └── otherwise                         → new: copied to storage, queued
```

Every file is read **once** (`path.read_bytes()`), hashed, and copied to
`backend/data/storage/uploads/historical/<uuid>/`. All later work uses the copy, so the original
folder is never opened for writing. The scan stores the complete inventory (file name, folder,
extension, size, status, reason, change) in `historical_scans.inventory`.

## 3. Different BOQ formats (`knowledge/extract.py`)

Each worksheet is examined on its own, in its own layout. Nothing is standardised or rewritten.

| Layout | How it is recognised | How parent → component is read |
|---|---|---|
| Sales BOM, imported | header row with `Unique No … ClassName, Item No.` (`find_header`) | `Unique No` / `ParentUniqueNo` chain: the nearest ancestor that is a billable line |
| Sales BOM, working copy | same header, `Unique No` blank | row order: components belong to the line above them; a Section row ends the line; context from `Cust Ref No.` prefixes |
| Sales BOM, draft | `Sales Item` blank on lines | a line is a non-component, non-section row with a Cost Sheet Type, unit or quantity |
| Any other layout | `boq_parser/layout.detect_layout` (header synonyms, sub-headers) | the Item-Number column is found by header or by **values** (`item_number_column`); rows with an Item Number under a BOQ item are its components (`parse_generic`) |

Rejected with an explicit reason instead of guessed at:

* **Flat component lists** (every described row has its own Item Number, e.g. an ERP export):
  `_flat_list` → "no parent BOQ descriptions".
* **Worksheets with no Item Master numbers** (client tender BOQs, OC/costing sheets, Job Code
  forms, summaries): no mapping knowledge exists in them.
* **Malformed styles / defined names**: read from a repaired in-memory copy
  (`excel_processor/workbook.open_values_tolerant`); noted on the file.

Every `ExtractedParent` keeps sheet, row, unique/serial number, description, ancestor context,
UOM, quantity, the method used (`hierarchy` / `row order` / `generic`) and its components with
their own row numbers. Component rows that sit under no BOQ item (typically overhead rows
directly under a section) are reported as `orphan_rows`, never attached to a wrong parent.

## 4. Scope filter and classification (`service._ingest`, `_resolve`)

For each component:

```
resolved type = Cost Sheet Type written in the verified BOQ
                (or, only when the engineer left it blank, the Item Master's type)
in scope      = resolved type ∈ ENABLED_COST_SHEET_TYPES (Highside Supply, Highside Installation)
```

* In-scope components are the Highside knowledge (counted, searchable, suggestible).
* Out-of-scope components (Lowside, Section, Mathadi, Miscellaneous…) are stored with
  `in_scope = false`. They are **never suggested**; they exist so a Lowside item is never
  promoted to Highside and so a BOQ line that looks like Lowside work is flagged for review.
* `master_cost_sheet_type` keeps the Item Master's own classification next to the historical one;
  the Item Master record itself is never changed.
* `type_source` says where the classification came from (`historical`, `item_master`).

## 5. Statuses

| Status | Meaning |
|---|---|
| `queued` (Pending) | waiting for the worker |
| `processing` | being extracted |
| `ingested` (Successfully Ingested) | Highside mappings stored and indexed |
| `partial` (Partially Ingested) | stored, but some Highside rows could not be learned (no parent row, unreadable worksheet); `error` and `summary.partial_reasons` say which |
| `no_mappings` | readable, but no Highside parent-component mapping in it; the reason says why (client/OC sheet, flat list, unmapped Sales BOM, Lowside only) |
| `failed` | could not be read or processed; the error is recorded; *Retry failed* requeues it |
| `duplicate` | byte-identical to another file, or the same mappings as a file already ingested |
| `changed` | a folder scan saw the source change and `reprocess_changed` was off |

Informational notes that do not make a file partial (for example Item Numbers that are not in the
current Item Master) are in `summary.notes`.

## 6. Database storage

One `knowledge_parents` row per parent description, with its components in
`knowledge_components` (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)). Each parent is linked to
its `historical_boqs` row, which holds the original path, relative path, project folder, scan,
SHA-256 and extraction date. So any suggestion can be traced to file → worksheet → row.

Reprocessing a file deletes that file's parents first, then stores the new extraction, in one
transaction: **no duplicate mappings, ever**.

## 7. Embeddings and vector search (`knowledge/index.py`)

`service._load_parents` loads every stored historical parent (statuses `ingested`/`partial`) and
review-feedback parent, collapses exact repeats (the same description with the same components in
a WORKING and a FINAL copy count once; `KnownParent.copies` remembers how many), and builds a
`KnowledgeSnapshot`:

* `_SubIndex` → `HybridTextIndex` over the first 600 characters of each description plus its
  section: word TF-IDF + character n-grams + LSA embeddings in FAISS (128 dimensions).
* Technical attributes (model numbers, capacity in HP/TR/kW, sizes, materials, equipment form,
  indoor/outdoor side, refrigerant, brand) were parsed at ingestion and are stored with the
  parent (`attrs._full`), so a rebuild does not re-parse thousands of descriptions.
* `SnapshotCache` rebuilds only when the historical or feedback version changes; the build state
  is visible in `GET /api/knowledge/stats` → `index`.

`KnowledgeSnapshot.evidence(text, context, attrs, …)` returns similar verified descriptions **only
if their attributes are compatible** (`compatibility`): a different model, capacity, size,
material, equipment form, side or refrigerant disqualifies a match however similar the wording.

## 8. Continuous knowledge expansion

| Source | How it is learned | Priority |
|---|---|---|
| New historical BOQ (upload or scan) | extracted, stored, index rebuilt automatically | 1 |
| Review approvals and corrections | `service.sync_feedback` after every review action keeps one `feedback` parent per reviewed line with exactly its approved components | 2 |
| Item Master | loaded per upload; historical files read without a master are re-read automatically when one arrives (`stale_after_new_master`) | 3 |
| AI / rule inference | only ranks and chooses among Item Master records | 4 |

Nothing relies on the model "remembering" files: knowledge exists only once it is in these tables
and in the rebuilt index. A file's status says whether it got there.

## 9. Duplicate detection

1. **Byte level** – SHA-256 of the file (`historical_boqs.sha256`): an identical upload is refused
   (409); an identical file found by a scan is recorded as `duplicate` and not processed.
2. **Mapping level** – `Extraction.fingerprint` (sorted parent descriptions + their Item Numbers):
   a re-saved copy with the same mappings becomes `duplicate`.
3. **Parent level** – near-identical versions of one project (WORKING vs FINAL) are both stored for
   traceability, but identical parents are collapsed in the index so they do not count twice.

## 10. Error handling and retries

* Every file is processed in its own transaction; an exception marks only that file `failed`
  with `ExceptionType: message`, and the worker continues with the next file.
* An unreadable worksheet is recorded on that worksheet; the rest of the workbook is still read.
* `POST /api/historical/retry-failed` re-reads failed folder files from their original location
  (picking up files that were locked or still syncing) and requeues them.
* `POST /api/historical/{id}/reprocess` re-extracts one file; `reprocess-all` all of them.
* Files queued or processing when the server stopped are requeued on start-up.
