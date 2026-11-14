# AI mapping logic

How one BOQ line becomes a list of Highside components with a Cost Sheet Type. Entry point:
`backend/app/services/ai_matching/engine.py: MatchingEngine.match_line(line)`, called for every
eligible line by `services/jobs.py: run_job`.

```
BOQ line + ancestor headings
  1 requirement      requirements.build_requirement         scope items, attributes
  2 eligibility      boq_parser/eligibility, engine.line_scope  unit+qty rule, Highside scope
  3 history          knowledge.index.KnowledgeSnapshot.evidence  similar *compatible* verified BOQs
  4 retrieval        engine._shortlist → retrieval.ComponentIndex   Item Master candidates
  5 scoring          scorer.score_candidate                  hard conflicts, then ranking
  6 decision         AI provider (shortlist only) or engine._decide_with_rules
  7 classification   validation.classify → knowledge.index.Classifier   priority rule
  8 validation       validation.finalize_line                re-check against the Item Master
  9 review status    jobs.initial_status                     nothing is auto-approved
```

## 1. BOQ description processing

`boq_parser/parser.py` gives every BOQ line its ancestors (section → sub-section), because the
meaning often sits there ("Supply & installation of copper piping" › "22 mm"). `build_requirement`
strips activity words, splits clauses ("including", "with", "+") into **scope items** (a main
equipment item plus extras such as installation), and records each item's own attributes, so a
size written for sleeves never becomes a size requirement for the pipe.

## 2. Technical attribute extraction

`item_master/normalize.py: extract_attrs` parses text (never the unreliable Size/Make/Model
columns of the master): model numbers (`RXQ38BRY16`, spaced forms joined), capacity (HP, TR, kW,
CFM, LPS…), sizes in mm (inch fractions converted), dimensions, sqmm/core for cables, gauge,
brand, material families, equipment form (cassette, ductable, hi-wall…), indoor/outdoor side and
refrigerant. The same parser reads BOQ lines, Item Master briefs and historical descriptions.

## 3. Eligibility and scope

A line is mapped only when it has both a valid unit and a valid quantity (`eligibility.check`).
`engine.line_scope` keeps only enabled Cost Sheet Types (`ENABLED_COST_SHEET_TYPES`, by default
Highside Supply and Highside Installation). Lines with a Lowside type are skipped as out of scope.

## 4. Historical BOQ retrieval (highest-priority evidence)

`KnowledgeSnapshot.evidence(text, context, attrs, known_items, enabled_types)`:

1. Search the historical index and the review-feedback index (`_SubIndex.search`: hybrid
   TF-IDF + character n-grams + LSA embeddings in FAISS; exact text = 1.0; below 0.42 ignored).
2. **Technical compatibility gate** (`index.compatibility`): a past description is discarded if it
   disagrees on model, equipment form, indoor/outdoor side, capacity (±5 %), size, dimensions,
   material family, refrigerant, brand or cable size. A shared model number adds 0.1 similarity.
   Similar keywords alone are never enough.
3. Each surviving parent votes for its components that exist in the current Item Master, weighted
   by similarity × source weight (historical 1.0, feedback 0.7). Result per Item No.:
   `ItemEvidence(weight, support, best_similarity, historical uses, type tallies, references)`.
4. `other_scope_share`: the weighted share of similar parents mapped **only** to non-Highside
   types — the signal that the line is really Lowside work.

## 5. Item Master retrieval and semantic matching

`engine._shortlist` merges several retrievals per scope item, each from the Item Master index for
the target Cost Sheet Type (`retrieval.MasterIndexes.index(type)`):
* the full scope wording (k=40) and the head noun plus sizes (k=25) — hybrid lexical + vector;
* exact model numbers from every master column (`by_models`);
* every Item No. that verified history or earlier approvals used for a similar line
  (`by_item_nos`), so known-good items are always considered.

Historical **classifications** also shape this index: `Classifier.overrides` moves an item to the
Cost Sheet Type verified BOQs use for it (e.g. an "INSTALLATION OF ODU" record the master calls
Lowside Installation is searched as Highside Installation), so it can be found at all.

## 6. Candidate ranking (`scorer.score_candidate`)

Technical compatibility decides; text similarity only ranks what survives.
* Hard conflicts reject a candidate whatever its wording: wrong model, size, material, make,
  capacity, equipment form, side.
* Score components: text similarity, attribute agreement, exact model hit, precedent bonus
  (`PRECEDENT_BONUS = 12`), **historical bonus** (`HISTORY_BONUS = 24`, scaled by
  `history_strength = best_similarity × min(1, 0.4 + support)`).
* An exact model outranks generic items (`_apply_model_gate`); if the model is missing from the
  master, the closest alternative is offered for review only.
* Below `MIN_SELECT_SCORE = 45` (installation: 60) a scope item is reported unmatched, not guessed.
* `_companions` adds components that ≥50 % of similar verified BOQs include but the line did not
  name (typically the installation item for an equipment line) — always for review.

## 7. Decision

With an AI provider (`AI_MODE` = claude / openai / gemini) the model receives one line, its
requirement, the shortlist and up to five compatible historical examples
(`LineEvidence.payload`), and must choose from the shortlist (schema-validated). Otherwise, or
after repeated provider failures, `_decide_with_rules` takes the best compatible candidate per
scope item. AI output is the lowest priority: it can only choose among Item Master records that
survived the compatibility checks.

## 8. Historical priority and conflict resolution (`knowledge.index.Classifier.resolve`)

For each selected component, the final Cost Sheet Type is decided in this order:

| # | Source | Rule |
|---|---|---|
| 1 | Verified historical BOQs **with similar, compatible descriptions** | the type used there, if ≥75 % agree (`CLEAR_MAJORITY`) |
| 1b | All verified historical uses of the item | majority type if ≥75 %; otherwise the most used type **flagged as a conflict for review** |
| 2 | Approved review decisions / corrections | same majority rule |
| 3 | Item Master | its own Cost Sheet Type |
| 4 | AI inference | never overrides the above |

The Item Master's value is kept next to the final one (`component_mappings.master_cost_sheet_type`),
and the reason is stored (`classification_source`, `classification_note`, `evidence` with source
file/sheet/row). The master file and its rows are never modified.

Example: the Item Master classifies `CO-16001 INSTALLATION OF ODU` as *Lowside Installation*;
verified BOQs with compatible VRV outdoor-unit descriptions use it as *Highside Installation* →
the suggestion is *Highside Installation*, `classification_source = historical`, with the Item
Master value shown alongside.

Historical types that are blank in the source BOQ are never treated as verified classifications
(the Item Master value is used and labelled `type_source = item_master`).

## 9. Highside Supply / Installation classification and scope safety

* `validation.finalize_line` drops any selection whose final type is not an enabled Highside type
  ("manual review needed" note) — a Lowside item is never relabelled to fit the scope.
* `engine._check_scope`: if ≥60 % of similar verified descriptions were mapped only to
  non-Highside types, or the best Item Master match is clearly Lowside, the line becomes
  `scope_review` and suggestions are withheld.

## 10. Confidence scoring

`Selection.confidence` (0–100) comes from the score above; it is a deterministic heuristic, not a
calibrated probability. Strong historical support raises it; conflicts, missing models, cost 0 or
UOM mismatches add flags. `jobs.initial_status`: any blocking flag, an inferred item, or (if the
run requires it) confidence below the threshold (default 75) → `review`; everything else
`pending`. Nothing is approved automatically.

## 11. Human review and feedback

Every review action (`approve`, `incorrect`, `replace`, `add`, `approve-all`, `cost-sheet-type`,
`remove`) is written to `mapping_approvals` and immediately calls
`knowledge.service.sync_feedback`, which keeps exactly one `feedback` knowledge parent per
reviewed line containing its approved components and final types. The feedback index is rebuilt
on the next run, so approved corrections are reused (priority 2) for similar lines in later BOQs.
