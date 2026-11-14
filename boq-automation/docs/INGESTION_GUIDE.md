# Ingestion guide

How to add verified BOQs to the knowledge base and check that they were learned.

## Before you start

* The backend must be running the current code (`cd backend && uvicorn app.main:app --port 8000`).
  A server started before this version must be restarted once; the database upgrades itself.
* Load the Item Master first (Upload page). Historical files read without one are re-read
  automatically when it arrives.
* Only `.xlsx` and `.xlsm` are read. Save `.xls` workbooks as `.xlsx` to include them.

## Upload individual BOQs

1. Open **Knowledge base**.
2. Drop one or several files on **Individual upload** (or click to browse).
3. Each file is copied, queued and processed automatically; the table refreshes every 2 seconds.
   An identical file that is already in the knowledge base is refused with its name.

API: `curl -F "file=@BOQ.xlsm" http://localhost:8000/api/historical`

## Bulk-ingest a folder

1. In **Bulk folder ingestion**, enter a folder path *as the server sees it*, e.g.
   `C:\Users\EIS\OneDrive\Desktop\BOQ Component Mapping\Kytes Projects`.
2. Leave *Reprocess files that changed* ticked to update changed files automatically.
3. Click **Scan folder**. Every subfolder is scanned; the progress bar shows processed/queued files.
4. **View latest folder inventory** lists every file found with its folder, type, size, status and
   reason (unsupported, ignored lock file, duplicate, failed, ingested …).

API:
```bash
curl -X POST http://localhost:8000/api/historical/scans -H "Content-Type: application/json" -d "{\"root_path\": \"C:/Users/EIS/OneDrive/Desktop/BOQ Component Mapping/Kytes Projects\"}"
```

Scanning the same folder again only processes what is new or changed (SHA-256 of each file);
unchanged files are skipped and nothing is stored twice. The folder itself is never modified.

## Check progress

* Metric strips: folders scanned, files discovered, ingested, processing, partial, failed;
  parent descriptions, Highside Supply / Installation mappings, unique Item Numbers, new (7 days),
  last ingestion.
* File table filters: Ingested · Partial · Processing · Failed · No Highside mappings ·
  Duplicates · Changed. The line under a file name is its folder; the red/grey line is its reason.
* API: `GET /api/knowledge/stats`, `GET /api/historical/scans/{id}`.

## Understand a file's result

Click a file name (or *View source & mappings*):
* source path, extraction date, Item Master it was checked against;
* **Worksheets read**: each worksheet's format, how parents and components were linked
  (`hierarchy`, `row order`, `generic`) or why it held nothing;
* Highside mappings (switch to *All read* to see Lowside/other components kept as out-of-scope
  evidence); every component shows its Item No., Cost Sheet Type, source row, whether the type
  came from the Item Master because the BOQ cell was blank, and Item Master disagreements.

| Status | What to do |
|---|---|
| Ingested | nothing |
| Partially ingested | read the reason (e.g. Highside rows under no BOQ item); fix the source and reprocess if needed |
| No Highside mappings | expected for client/OC sheets, Job Code forms, summaries, unmapped or Lowside-only BOQs |
| Failed | read the error; close the file in Excel / wait for OneDrive, then **Retry failed** |
| Duplicate | nothing; the original is used |
| Changed – reprocess | choose **Reprocess updated file** |

## Retry failures

* **Retry failed (n)** in the page header, or *Retry* in a file's menu.
* API: `POST /api/historical/retry-failed`. Folder files are re-read from their original location.

## Reprocess updated files

* Rescan the folder (changed files are detected and reprocessed), or
* *Reprocess* / *Reprocess updated file* in a file's menu (`POST /api/historical/{id}/reprocess`), or
* *Replace file* for uploaded files (`PUT /api/historical/{id}/file`), or
* **Reprocess all** after the extraction logic itself changes.

The file's previous mappings are replaced in one transaction; no duplicates are created.

## Verify newly indexed mappings

1. **Search historical mappings** (bottom of the page): search by description words, Item No. or
   Cost Sheet Type; each result names its source file, worksheet and row.
2. **Check what the knowledge base would suggest for a new description**: paste a BOQ line; you
   see the similar, technically compatible verified examples the engine will use, with similarity
   and agreeing attributes (`GET /api/knowledge/search?q=...`).
3. `GET /api/knowledge/stats` → `index.up_to_date: true` and `index.historical_parents` show the
   vector index includes the latest files (`POST /api/knowledge/index/rebuild` builds it now).
4. Run a mapping: the run summary shows how many historical descriptions were available, and each
   suggestion that history supported shows its evidence and `classification_source = historical`.
