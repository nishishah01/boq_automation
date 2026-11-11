# Workbook findings

From inspecting `BOQ_IMPORT_REST_PROD_V15 (12).xlsm` and `ITEM MASTER Component.xlsx`.

## Decisions needed from the business

### 1. The real BOQ has almost no Highside work

Of 19 sales lines (Sales Item = Y) in the supplied BOQ:

| Column H | Lines |
|---|---|
| Lowside Supply | 14 |
| Lowside Installation | 3 |
| Highside Supply | 1 (already mapped) |
| Miscellaneous Items | 1 |
| Highside Installation | 0 |

With the agreed Highside-only scope, this BOQ produces **no new mappings**. The brief's main example, PVC drain piping, is Lowside Supply in both the BOQ and the Item Master. The first version follows the agreed scope. Adding the Lowside types takes one configuration line (`ENABLED_COST_SHEET_TYPES`) and no code.

### 2. Installation items are missing from the Item Master

All 14 installation children in the BOQ use `IN-` codes (for example `IN-00599`). **The Item Master has no `IN-` records at all:** every one of its 18,932 Item Nos starts with `CO-`. Since an Item No. must come from the master, most installation scope cannot be mapped from this file and is reported as unmatched.

Fix: export the master with `IN-` items included, in the same columns. No code change is needed.

### 3. Highside Installation is nearly empty, and practice contradicts it

- The master has only 8 Highside Installation records. At least 3 look misfiled as installation: `CO-01358 DAIKIN FXFSQ32ARV16 IDU UNIT`, `CO-11712 DAIKIN RXQ10BRY17 ODU UNIT` and `CO-18198 DX HI WALL 2.0 TR`.
- The BOQ books the installation of a Highside indoor unit as **Lowside Installation** (row 11, `IN-09490`). So "Highside Installation" may not be how the company actually classifies this work.

### 4. Equipment you may expect in Highside is Lowside

No chiller or cooling tower record is Highside Supply. All are Lowside Supply (`CO-10880`, `CO-00655`). Some models are split too: `RXQ38BRY16` is Highside Supply but `RXQ38ARY6` is Lowside Supply. Under strict filtering, those lines report as unmatched.

## Sales BOM structure

The header is on row 2 and data runs from row 3 (60 rows here). Rows 63 to 9,940 are empty styled rows.

| Row type | How to recognise it | Count |
|---|---|---|
| Section | G=N, H=Section | 8 |
| Sales line (the "parent description") | G=Y | 19 |
| Child | U=Component or Installation | 33 (19 `CO-`, 14 `IN-`) |

- Hierarchy comes from `Unique No` and `ParentUniqueNo` (`1.5` > `1.5.1` > `1.5.1.1`). Row position and blank rows mean nothing.
- **The meaning of a line often sits in its parent section.** Line `1.5.1` is just "40mm"; "Supply & Installation of PVC Drain Piping with Insulation" is the section above it. The parser gives every line its ancestor chain.

### Child row conventions

Column E, I and M inheritance holds on 29 of the 33 children. The other 4 sit under "OVERHEAD ITEMS", which is Miscellaneous Items and out of scope.

| Column | Value |
|---|---|
| A / B | Parent number + running number / parent number |
| D | Item Brief exactly as in the master |
| E | The **parent's** Description, verbatim |
| G | N |
| H | The component's own Cost Sheet Type |
| I | Parent's Billable Qty |
| M | Parent's UOM, **not** the master's (BOQ RFT against master RMT) |
| N | Cost |
| L, Q, R, S, T | The sheet's row formulas |
| U | `Component` for `CO-`, `Installation` for `IN-` |
| V | Item No. |

The macro that imports items writes only D, U and V; its lines for H and M are commented out. That is why column H is a manual step today.

Assumption to confirm: a `CO-` item listed under an installation Cost Sheet Type gets ClassName `Component`, because the prefix rule holds on every row seen. The BOQ has no such row to prove it.

Estimators override cost: BOQ values differ from master values (refrigerant piping 678.53 against 390). Exported children take the master cost, and 0 where the master has none, so **estimators must still price those rows**.

## Item Master quality

18,932 records, 40 columns.

| Finding | Count | Handling |
|---|---|---|
| Highside Supply / Highside Installation | 1,083 / 8 | The searchable scope |
| Blank Cost Sheet Type | 34 | Never auto-selected; noted for review |
| Records whose exact brief also appears on another record | 1,933 | Best-populated record chosen; others listed |
| Groups of duplicate briefs after normalising spelling | 824 | As above |
| Duplicate groups that disagree on Cost Sheet Type | 84 | Item's own record decides; flagged |
| Cost of 0 | 14,431 (76%) | Flagged; Column N stays 0 |
| "INSTALLATION" wording under a Supply type | 26 | Flagged; not used as supply |
| Retired items (`NA` / `N/A` prefix) | 7 | Penalised and flagged |

- `Size`, `Make` and `Model` are unreliable: `Model` holds values like "wall", "UNIT", "CHARGES". Attributes are parsed from Item Brief instead.
- Spelling varies: INSTALATION, INSTALLATON, REMORT, CONTROLLAR, CANVASS. These are normalised for matching only. The original text is what gets exported.

## Why the workbook is edited as XML

`openpyxl` is suggested in the brief, so it was tested with a real load-and-save of this workbook using `keep_vba=True`:

| Feature | After saving with openpyxl |
|---|---|
| The 5 column dropdowns (x14 validations on G, H, M, W) | All 5 lost |
| Macro buttons (Convert, Validate, Reset...) | Lost: the drawing is dropped |
| Dynamic-array metadata used by Summary formulas | Lost on all 29 cells |
| customXml parts and printer settings | Lost |
| VBA | Kept |

So `openpyxl` only reads. Export edits the package directly and changes only the Sales BOM sheet, shared strings, `workbook.xml` and the two calcChain references; all other parts stay byte-identical. Every export is verified before it can be downloaded.
