# BOQ Component Mapping

Maps BOQ lines to Item Master components, learns from verified historical BOQs and review decisions, lets engineers review every choice, and writes approved components into a copy of the original workbook with its macros and formatting intact.

Any BOQ layout is accepted: the company's Sales BOM template and client BOQs with their own sheet names, column names and column order. Only BOQ items with both a unit and a quantity are mapped; the rest are listed as `Skipped – Unit/Quantity Missing`.

Technical documentation: [architecture](docs/PROJECT_ARCHITECTURE.md), [codebase guide](docs/CODEBASE_GUIDE.md), [AI mapping logic](docs/AI_MAPPING_LOGIC.md), [historical knowledge base](docs/HISTORICAL_KNOWLEDGE_BASE.md), [database schema](docs/DATABASE_SCHEMA.md), [API reference](docs/API_REFERENCE.md), [ingestion guide](docs/INGESTION_GUIDE.md), [changelog](docs/CHANGELOG.md).

Read [docs/PHASE1_FINDINGS.md](docs/PHASE1_FINDINGS.md) first: it covers scope decisions the business needs to make. Accuracy results are in [docs/EVALUATION.md](docs/EVALUATION.md).

## Run it

Requires Python 3.11+ and Node 20+.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. To try it, upload the workbooks in `sample_data/`.

With no `DATABASE_URL`, the app uses local SQLite. For PostgreSQL, run `docker compose up -d` and set `DATABASE_URL` in `backend/.env`. Tables are created on startup.

### Enable AI matching

Without a key the app runs rule-based, and the sidebar says so. To use a model, set one of these in `backend/.env` and restart the backend:

| Provider | Settings | Default model |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY=...` | `claude-opus-5-5` (`ANTHROPIC_MODEL`) |
| OpenAI | `AI_PROVIDER=openai` and `OPENAI_API_KEY=...` | `gpt-5.5` (`OPENAI_MODEL`) |
| Gemini | `AI_PROVIDER=gemini` and `GEMINI_API_KEY=...` | `gemini-3.8-flash` (`GEMINI_MODEL`) |

Both providers get the same prompt and the same shortlist, and their answers pass the same Item Master checks. If the provider is unavailable, a line falls back to rule scoring and is forced to manual review. For Claude, server-side refusal fallback is on.

## How matching works

Accuracy against real engineer-mapped BOQs, and what changed, is in [docs/MATCHING_IMPROVEMENTS.md](docs/MATCHING_IMPROVEMENTS.md). Re-run it with `python scripts/evaluate_real.py --item-master <master.xlsx> --boq <mapped.xlsm>`.


1. **Understand.** Each sales line is combined with its parent sections to extract make, model, size, capacity, material and activities, then split into scope items (for example pipe supply, pipe installation, insulation).
2. **Recall.** The knowledge base is searched for similar verified descriptions. A past description counts only when its model, capacity, size, material and equipment type are compatible with the line.
3. **Filter.** Each scope item searches only the FAISS index for its Cost Sheet Type. Records are filed under the type verified BOQs give them, so an item the Item Master lists as Lowside but verified BOQs use as Highside is found.
4. **Retrieve.** Word and character TF-IDF blended with LSA embeddings. Exact model hits and components used for similar verified descriptions always make the shortlist. Set `EMBEDDING_BACKEND=sentence-transformers` for neural embeddings.
5. **Score.** A wrong size, material, model, make or capacity removes a candidate regardless of wording or history. Verified use for a compatible description adds weight and, when strong, decides among compatible items.
6. **Decide.** The AI model chooses from the shortlist and the verified examples with a Pydantic-validated answer, or the rule engine picks the top score. Components that similar verified BOQs consistently include (an installation item for an equipment line, say) are suggested for review.
7. **Scope check.** A line that looks like Lowside or other work (by verified examples, the Item Master, or the AI) is flagged `Review – outside Highside scope` and gets no automatic components.
8. **Validate and classify.** Every Item No. is re-read from the master; anything unknown is discarded. The Cost Sheet Type follows the priority below, and the Item Master's value is kept beside it.

### Knowledge base and classification priority

Add completed, verified BOQs on the **Knowledge base** page: upload files (several at once, `.xlsx` or `.xlsm`, any layout) or **scan a folder** and all its subfolders. Processing starts automatically. Each file's parent descriptions with their **Highside Supply / Highside Installation** components, Item Numbers and Cost Sheet Types are extracted with their source file, folder, sheet and rows, stored in the database and indexed; Lowside and other components are kept only as out-of-scope evidence. A file counts as learned only when its status is **Ingested** or **Partially ingested**. Identical files are refused or marked **Duplicate**; rescanning a folder only processes new or changed files. Files can be searched, viewed, retried, reprocessed, replaced or removed. See [docs/INGESTION_GUIDE.md](docs/INGESTION_GUIDE.md).

Cost Sheet Type priority, highest first:

1. Verified historical BOQs (the closest technical context first; disagreement is flagged for review).
2. Corrections and approvals made in this app's review (stored as feedback after every review action).
3. The Item Master record.
4. AI or rule inference.

Item Numbers always come from the Item Master. Each suggestion keeps the Item Master type, the recommended type, how it was decided, and the verified examples behind it.

Nothing is auto-approved. Items with data problems or low confidence get status `review` and must be approved one by one; **Approve all pending** skips them.

## Export safety

- Uploads are stored in a new folder, and the writer refuses to overwrite its source file.
- Approved components are inserted directly below their BOQ item and highlighted yellow across the row. Sales BOM template: the template's own columns. Other BOQs: the description column holds the component description, and existing `Cost Sheet Type` / `Component Item No.` columns are used or, if missing, added to the right of the data.
- Inserting rows moves every reference the way Excel's Insert Row does: formulas on all sheets, totals, defined names, merged cells, conditional formats, validations, hyperlinks, tables, comments, drawings and charts.
- Item Numbers and descriptions are re-read from the Item Master at export time; the Cost Sheet Type is the reviewed one. Only approved components are written.
- Each export is verified before download: macro hash, every original row at its new position, every inserted row against the master and the run's Cost Sheet Types, and sheet protection, validations, drawing and filter. A failure blocks the download.
- `calcChain.xml` is removed and full recalculation is set, so Excel rebuilds the Summary totals on open.

## Tests and evaluation

```bash
cd backend
pytest
python scripts/evaluate.py --sample-dir ../sample_data --out ../docs/EVALUATION.md
```

Regenerate the samples from the originals, which are only read:

```bash
python scripts/generate_samples.py --boq "<BOQ.xlsm>" --item-master "<ITEM MASTER.xlsx>" --out ../sample_data
```

## Extending scope

Add a type to `ENABLED_COST_SHEET_TYPES`. Types not yet in `app/core/cost_sheets.py` need one line there.

## Known limits

- No login: every action is recorded against one `local` user. Add authentication before wider rollout.
- Jobs run in an in-process thread pool. A restart marks running jobs as failed; they are not resumed.
- The knowledge index is rebuilt in memory from the database when the knowledge changes (embeddings are not persisted). Historical BOQs uploaded before any Item Master are reprocessed automatically when one becomes ready.
- Confidence scores from the rule engine are uncalibrated heuristics.
- `sample_data/ground_truth.json` was written by the developer, not verified by an engineer.
