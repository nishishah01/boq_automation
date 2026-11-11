"""Build sample_boq.xlsm, sample_item_master.xlsx and ground_truth.json from the real workbooks.

The originals are only read. The sample BOQ keeps the template's sheets, styles, macros and
validations, with new Sales BOM rows. The sample Item Master is a filtered copy of real rows:
no record is synthetic and every Item No. in the ground truth is checked against the master.

    python scripts/generate_samples.py --boq "<BOQ.xlsm>" --item-master "<ITEM MASTER.xlsx>" --out ../sample_data
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.excel_processor.ooxml import (  # noqa: E402
    Formula, RawRow, SharedStrings, SheetDoc, XlsxPackage, cell_styles, relocate_row, render_row_body,
)
from app.services.excel_processor.workbook import open_values  # noqa: E402
from app.services.item_master.processor import read_item_master  # noqa: E402

HS, HI, LS = "Highside Supply", "Highside Installation", "Lowside Supply"

# (unique no, text, kind, cost sheet type, qty, uom, expected items, case tag)
# expected: list of alternatives per required component; [] means nothing should be selected.
S = "section"
Y = "line"
C = "child"
ROWS = [
    ("1", "Sample Project - Highside Test BOQ", S, "", 0, "", None, ""),
    ("1.1", "Supply of Daikin Make NVRV Indoor & Outdoor Units", S, "", 0, "", None, ""),
    ("1.1.1", "DX Ductable machine (High Static) - Non-Inverter - R410A - Daikin", S, "", 0, "", None, ""),
    ("1.1.1.1", "11.0 Tr - FDR130FRV16 Remote Controller Included", Y, HS, 6, "NOS", None, "existing-mapping"),
    ("1.1.1.1.1", "DAIKIN FDR130FRV16 IDU UNIT", C, HS, 6, "NOS", "CO-01185", ""),
    ("1.1.1.2", "2.5 Tr - FDMF30CRV16", Y, HS, 4, "NOS", [["CO-01170"]], "model, section context"),
    ("1.1.1.3", "11.0 Tr - FDR130GRV16", Y, HS, 2, "NOS", [], "model absent; near-identical models must be rejected"),
    ("1.1.2", "VRV Outdoor Units - Daikin", S, "", 0, "", None, ""),
    ("1.1.2.1", "38 HP - RXQ38BRY16", Y, HS, 1, "NOS", [["CO-21405"]], "model and capacity"),
    ("1.1.2.2", "22 HP - RXQ22BRY16", Y, HS, 2, "NOS", [["CO-21726", "CO-13681"]], "model and capacity; two records share the model"),
    ("1.1.2.3", "16 HP - RXQ16BRY16", Y, HS, 1, "NOS", [["CO-21725"]], "model and capacity"),
    ("1.1.3", "Indoor Units - Daikin", S, "", 0, "", None, ""),
    ("1.1.3.1", "Hi-Wall FXAQ32ARVE6", Y, HS, 8, "NOS", [["CO-01262", "CO-19703", "CO-19704", "CO-16547"]],
     "ambiguous: duplicate records in master"),
    ("1.1.3.2", "Ductable FXMQ50ARV16 1.65 TR", Y, HS, 5, "NOS", [["CO-19485", "CO-19484", "CO-19483", "CO-01427"]],
     "ambiguous: duplicate records in master"),
    ("1.2", "Supply of Mitsubishi Make Units", S, "", 0, "", None, ""),
    ("1.2.1", "Mitsubishi MS-GK18VA Indoor Unit", Y, HS, 3, "NOS", [["CO-02606"]], "make and hyphenated model"),
    ("1.2.2", "Mitsubishi FDTS71GA-W6 Indoor Unit", Y, HS, 3, "NOS", [["CO-17135"]], "make and hyphenated model"),
    ("1.3", "Supply of DX units by capacity", S, "", 0, "", None, ""),
    ("1.3.1", "Ductable Unit 8 TR", Y, HS, 2, "NOS", [["CO-16804"]], "capacity"),
    ("1.3.2", "Ductable Unit 4 TR", Y, HS, 2, "NOS", [["CO-16807"]], "capacity"),
    ("1.3.3", "Ductable Unit 6 TR", Y, HS, 1, "NOS", [], "capacity absent; other TR must be rejected"),
    ("1.3.4", "Outdoor Unit 4 TR", Y, HS, 2, "NOS", [["CO-16797"]], "synonym and capacity"),
    ("1.3.5", "4 Way Cassette Unit 1.6 TR", Y, HS, 4, "NOS", [["CO-16800"]], "capacity"),
    ("1.4", "Air Handling Units", S, "", 0, "", None, ""),
    ("1.4.1", "Supply of Air Handling Unit 10000 CFM", Y, HS, 1, "NOS", [["CO-19138"]], "synonym and capacity"),
    ("1.4.2", "Supply of AHU 4000 CFM", Y, HS, 1, "NOS", [["CO-19132"]], "capacity"),
    ("1.5", "Supply & Installation of Indoor Units", S, "", 0, "", None, ""),
    ("1.5.1", "Supply & Installation of Daikin FXFSQ125ARV16 IDU Unit", Y, HS, 2, "NOS",
     [["CO-01354", "CO-20086"], ["CO-11786"]], "combined supply and installation"),
    ("1.5.2", "SITC of Daikin FDMF30CRV16 IDU Unit", Y, HS, 2, "NOS", [["CO-01170"], ["CO-18012"]],
     "combined; SITC abbreviation"),
    ("1.5.3", "Supply and Installation of Daikin RR100ERY16 IDU Unit", Y, HS, 1, "NOS", [["CO-01551"], ["CO-04045"]],
     "combined supply and installation"),
    ("1.6", "Installation of Equipment", S, "", 0, "", None, ""),
    ("1.6.1", "Installation of Daikin FDMF30CRV16 IDU Unit", Y, HI, 4, "NOS", [["CO-18012"]], "installation only"),
    ("1.6.2", "Installation of Mitsubishi Tower Unit", Y, HI, 2, "NOS", [["CO-07738"]], "installation only; master typo"),
    ("1.6.3", "Installation of Daikin FXFSQ32ARV16 IDU Unit", Y, HI, 2, "NOS", [["CO-01358"]],
     "master lists an equipment record as installation; must go to review"),
    ("1.7", "Accessories and Electrical", S, "", 0, "", None, ""),
    ("1.7.1", "Supply of Armoured Cable 6 Sqmm x 4 Core", Y, HS, 120, "RMT", [["CO-16773"]], "cable size"),
    ("1.7.2", "Supply of Armoured Cable 10 Sqmm x 4 Core", Y, HS, 80, "RMT", [["CO-16772"]], "cable size"),
    ("1.7.3", "Refnet Joint KHRP26A32T6", Y, HS, 10, "NOS", [["CO-19878"]], "model"),
    ("1.7.4", "Daikin Remote Controller BRC7M530W6", Y, HS, 6, "NOS", [["CO-14013"]], "model"),
    ("1.7.5", "Gas Tight Joint SDGTC1915", Y, HS, 4, "NOS", [["CO-14596", "CO-21245"]], "model; near-duplicate records"),
    ("1.7.6", "GI Cable Tray 150 mm", Y, HS, 60, "RMT", [["CO-18004"]], "material and size"),
    ("1.7.7", "2 Way Motorised Valve 200 mm", Y, HS, 2, "NOS", [["CO-16146"]], "size"),
    ("1.7.8", "Refrigerant Piping 34.9 mm with Insulation", Y, HS, 40, "RMT",
     [["CO-21711"], ["CO-19622", "CO-02250"]], "multiple components; explicit insulation"),
    ("1.8", "Items with no Highside record", S, "", 0, "", None, ""),
    ("1.8.1", "Water Cooled Screw Chiller 300 TR", Y, HS, 1, "NOS", [], "no match: chillers are Lowside in the master"),
    ("1.8.2", "Cooling Tower 500 TR", Y, HS, 1, "NOS", [], "no match: cooling towers are Lowside in the master"),
    ("1.9", "Supply & Installation of PVC Drain Piping with Insulation", S, "", 0, "", None, ""),
    ("1.9.1", "40mm", Y, LS, 250, "RFT", None, "out of scope: Lowside"),
]

KEEP_LOWSIDE = re.compile(r"PVC DRAIN|COPPER|CHILLER|COOLING TOWER|REFRIGERANT|INSULATION|DUCTABLE|CABLE TRAY", re.I)


def build_boq(src: Path, dst: Path, by_no: dict) -> list[dict]:
    pkg = XlsxPackage(src)
    part = pkg.sheet_part("Sales BOM")
    doc = SheetDoc.parse(pkg.text(part))
    rows = doc.row_map()
    styles = {S: cell_styles(rows[4]), Y: cell_styles(rows[6]), C: cell_styles(rows[7])}
    attrs = re.sub(r'\sht="[^"]*"|\scustomHeight="1"', "", rows[63].attrs)
    sst = SharedStrings(pkg)

    new_rows, truth = [r for r in doc.rows if r.number < 3], []
    n = 2
    for uno, text, kind, cst, qty, uom, expected, tag in ROWS:
        n += 1
        parent = uno.rsplit(".", 1)[0] if "." in uno else ""
        section = kind == S
        rate = 1000 if kind == Y else 0
        cost = 0
        v = {"A": uno, "B": parent, "D": text, "E": text, "G": "Y" if kind == Y else "N",
             "H": "Section" if section else cst, "I": qty, "J": 0, "K": 0, "M": "SECTION" if section else uom,
             "N": cost, "O": rate, "L": Formula(f"IFERROR(J{n}+K{n},0)", 0),
             "Q": Formula(f"IFERROR((O{n}/N{n}-1)*100,0)", 0), "R": Formula(f"IFERROR(N{n}*Q{n}/100,0)", 0),
             "S": Formula(f"IFERROR(ROUND(N{n}*L{n},2),0)", 0), "T": Formula(f"IFERROR(ROUND(O{n}*I{n},2),0)", rate * qty)}
        if kind == C:
            master = by_no[expected]
            v.update({"D": master.item_brief, "E": ROWS[[r[0] for r in ROWS].index(parent)][1], "U": "Component",
                      "V": master.item_no, "H": master.cost_sheet_type})
        new_rows.append(RawRow(n, attrs, render_row_body(v, n, styles[kind], sst)))
        if kind == Y and isinstance(expected, list):
            for group in expected:
                for item_no in group:
                    if item_no not in by_no:
                        raise SystemExit(f"Ground truth references {item_no}, which is not in the Item Master")
            truth.append({"unique_no": uno, "description": text, "cost_sheet_type": cst, "case": tag,
                          "expected": [[{"item_no": i, "item_brief": by_no[i].item_brief,
                                         "cost_sheet_type": by_no[i].cost_sheet_type} for i in g] for g in expected]})
    shift = n - 62
    for r in doc.rows:
        if r.number > 62:
            new_rows.append(relocate_row(doc, r, r.number + shift))
    doc.rows, doc.shared = new_rows, {}
    last = new_rows[-1].number
    doc.head = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:X{last}"/>', doc.head)
    doc.tail = re.sub(r'<autoFilter ref="[^"]*"', f'<autoFilter ref="A2:X{n}"', doc.tail)
    pkg.set_text(part, doc.serialize())
    wb = pkg.text("xl/workbook.xml")
    pkg.set_text("xl/workbook.xml", re.sub(r"('Sales BOM'!\$A\$2:\$X\$)\d+", rf"\g<1>{n}", wb))
    sst.flush()
    pkg.drop_calc_chain()
    pkg.force_full_recalc()
    pkg.save(dst)
    return truth


def build_item_master(src: Path, dst: Path, required: set[str]) -> int:
    """Real rows only, with their original cells, styles and Sr No values."""
    wb = open_values(src)
    rows = wb.worksheets[0].iter_rows(values_only=True)
    header = list(next(rows))
    i_no, i_brief, i_cst = (header.index(h) for h in ("Item No", "Item Brief", "Cost Sheet Type"))
    keep = {1}
    for r, v in enumerate(rows, start=2):
        cst = v[i_cst] or ""
        brief = str(v[i_brief] or "")
        if v[i_no] in required or cst in (HS, HI, "Lowside Installation", "") or KEEP_LOWSIDE.search(brief):
            keep.add(r)
    wb.close()
    pkg = XlsxPackage(src)
    part = pkg.sheet_part(wb.sheetnames[0])
    doc = SheetDoc.parse(pkg.text(part))
    new_rows = []
    for row in doc.rows:
        if row.number in keep:
            new_rows.append(relocate_row(doc, row, len(new_rows) + 1))
    doc.rows = new_rows
    doc.head = re.sub(r'<dimension ref="([A-Z]+\d+):([A-Z]+)\d+"/>', rf'<dimension ref="\1:\g<2>{len(new_rows)}"/>', doc.head)
    pkg.set_text(part, doc.serialize())
    pkg.save(dst)
    return len(new_rows) - 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boq", required=True, type=Path)
    ap.add_argument("--item-master", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    by_no = {c.item_no: c for c in read_item_master(args.item_master)}
    truth = build_boq(args.boq, args.out / "sample_boq.xlsm", by_no)
    required = {e["item_no"] for t in truth for g in t["expected"] for e in g} | {"CO-01185"}
    count = build_item_master(args.item_master, args.out / "sample_item_master.xlsx", required)
    (args.out / "ground_truth.json").write_text(json.dumps({
        "provenance": "Written by the developer against real Item Master records. NOT engineer-verified; "
                      "have a BOQ engineer review it before quoting accuracy from it.",
        "lines": truth}, indent=1), encoding="utf-8")
    print(f"sample_boq.xlsm: {sum(r[2] == Y for r in ROWS)} sales lines; "
          f"sample_item_master.xlsx: {count} real records; ground truth: {len(truth)} lines")


if __name__ == "__main__":
    main()
