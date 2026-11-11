# BOQ Component Mapping

Maps Sales BOM lines to Item Master components, lets engineers review every choice, and writes approved components into a copy of the original workbook with its macros and formatting intact.

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
2. **Filter.** Each scope item searches only the FAISS index for its Cost Sheet Type.
3. **Retrieve.** Word and character TF-IDF blended with LSA embeddings. Exact model hits always make the shortlist. Set `EMBEDDING_BACKEND=sentence-transformers` for neural embeddings.
4. **Score.** A wrong size, material, model, make or capacity removes a candidate regardless of wording.
5. **Decide.** The AI model chooses from the shortlist with a Pydantic-validated answer, or the rule engine picks the top score. A shortlist is at most 12 candidates per scope item.
6. **Validate.** Every Item No. and Cost Sheet Type is re-read from the master. Anything unknown is discarded.

Nothing is auto-approved. Items with data problems or low confidence get status `review` and must be approved one by one; **Approve all pending** skips them.

## Export safety

- Uploads are stored in a new folder, and the writer refuses to overwrite its source file.
- Values are re-read from the Item Master at export time. Only approved components are written.
- Each export is verified before download: macro hash, every original row, every imported row against the master, and sheet protection, validations, drawing and filter. A failure blocks the download.
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
- Confidence scores from the rule engine are uncalibrated heuristics.
- `sample_data/ground_truth.json` was written by the developer, not verified by an engineer.
