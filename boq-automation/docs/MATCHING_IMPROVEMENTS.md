# Matching accuracy: audit and before/after results

## How this was measured

The answers come from engineers, not from the developer: three real BOQs from Downloads, `(12)`, `(20)` and `(21)`, each with components already chosen by the company's engineers. For every sales line the existing components were hidden, the engine was run, and its choices were compared with theirs.

- Only `CO-` items in the Item Master can be scored. The 40 installation (`IN-`) items in these BOQs are not in the supplied master, so no engine can find them.
- Two questions are measured separately: was the right item **retrieved** at all, and was it then **selected**.
- Script: `backend/scripts/evaluate_real.py`. The "old" engine is a snapshot taken before these changes.

## Results

### Highside lines (the app's current scope): 11 lines, 8 scorable components

| | Old engine | New, rules only | New, with Gemini |
|---|---|---|---|
| Correct components | 2 of 8 | **8 of 8** | **8 of 8** |
| Precision | 25% | **89%** | **80%** |
| Correct item retrieved | 62.5% | **100%** | **100%** |
| Model-number lines correct | 25% | **100%** | **100%** |

The extra picks: `GAS TIGHT JOINT ODU` (Highside) where engineers used the identical `GAS TIGHT JOINT` filed under Lowside; and, with Gemini only, a generic one-way cassette for `FXKQ40ARV16`, whose exact record is filed under Lowside. That pick is flagged "exact model not in the Item Master… for review only" and cannot be bulk-approved.

### All four Highside and Lowside types: 59 lines, 40 scorable components (rules only)

| | Old engine | New engine |
|---|---|---|
| Correct components | 14 of 40 | **20 of 40** |
| Precision | 35% | **49%** |
| Correct item retrieved | 72.5% | **82.5%** |
| Lines exactly right | 27.5% | **45%** |
| Model-number lines correct | 2 of 10 | **8 of 10** |

The two model lines still missed are master inconsistencies: `FXKQ40ARV16` and `FXCQ80BVM6` are filed as Lowside Supply while the BOQs book them as Highside.

These sets are small (11 and 59 lines). Treat the numbers as a baseline to track, not a guarantee.

## Root causes found, and fixes

| # | Root cause | Stage | Fix |
|---|---|---|---|
| 1 | A line's own text (`2.01 TR FXKQ63ARV16`) fell into the section's "including accessories…" clause and was discarded, model and all | Description understanding | The line's own text always stays in the main scope item. Only clause markers split off further components |
| 2 | Exact-model lookup only read the search text, which no longer held the model | Retrieval | Models are looked up directly from the extracted requirement, across Item Brief, Description and the Model column |
| 3 | A generic item with closer wording could outrank the exact model | Ranking | Model gate: when an exact-model record exists, generic items are rejected. When it does not, nothing is chosen silently; the closest alternative is flagged for review |
| 4 | "One way" and "4 way" cassettes, and indoor and outdoor units, were treated as interchangeable | Compatibility | Hard equipment-type and indoor/outdoor rules |
| 5 | Models written as `FXAQ 32 ARV16` or `FXAQ-32-ARV16` were not recognised | Model extraction | Joined forms are accepted when that exact model exists in the master |
| 6 | Spelling and terminology gaps: ALPHA/ALFA, Installastion/Installtion, Dismantle, DGT, R-410/R 410 A, Cabling, "2 C + 1 Sq.mm", table top structure | Normalisation | Added to the shared terminology table; both sides normalised alike |
| 7 | Sizes from a clause ("with 19 mm sleeves") became a size requirement for the pipe | Attribute extraction | Each scope item carries its own attributes |
| 8 | "Clamped along with refrigerant piping" was read as a second component | Description understanding | Fixing references after clamp, support, route and similar verbs are not split off |
| 9 | Gas for a "38 HP" system was rejected for not stating HP | Compatibility | Capacity is required only of equipment, not of gas, pipe or cable |
| 10 | Plurals blocked retrieval (SLEEVES against SLEEVE) | Retrieval | Text search is plural-insensitive, plus a noun-and-size query |
| 11 | "Remote controller included" was added as a separate component | Selection | Included items are listed as candidates only; real BOQs price them into the unit |
| 12 | Weak installation picks (lag coating for anti-corrosion coating) | Selection | Installation items need a higher score, because the master holds few of them |

## Learning from engineers

Approvals, "mark incorrect" and replacements are recorded already. They now feed back into matching:

- An item approved before for the **same line text under the same section** gets extra weight, and its reason says so.
- An item marked incorrect or replaced on that line is rejected there in future.
- A precedent never bypasses a technical check. An approved 8 TR unit is still rejected for a 4 TR line.

Matching is by exact line text, deliberately: the right item can depend on size, model or project. The benefit therefore grows as similar BOQs are reviewed, and it explains preferences such as `PVC DRAIN PIPE` over `PVC PIPE`, which no rule can infer.

## Explainability

Each line on the Review page now has **Candidates considered**. It lists the selected item, the viable alternatives with their scores and reasons, and the nearest rejected items with the reason each was rejected (for example "Generic item: exact model FXKQ63ARV16 is in the Item Master" or "Equipment type CASSETTE_4WAY differs from required CASSETTE_1WAY"). Exact-model matches are labelled.

## What still limits accuracy

- **Installation items:** none of the 40 `IN-` items is in the supplied master.
- **Master inconsistencies:** the same item under several Item Nos (three "REFRIGERANT GAS R410A" records), and equipment filed under Lowside that BOQs book as Highside.
- **Company preferences:** for example `REFRIGERANT PIPING` over `COPPER REFRIGERANT PIPING`. These are learned once engineers approve them.
- **Scope policy:** insulation named in a line ("…with insulation") is still suggested, as the brief requires, although these engineers price it into the pipe item.
