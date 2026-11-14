"""Excel export: what reaches the workbook, and what must survive it."""
import hashlib
import re
import zipfile

import openpyxl
import pytest

from app.services.boq_parser.parser import parse_boq
from app.services.excel_export.exporter import ChildPlan, export_boq
from app.services.excel_export.verifier import verify_export
from app.services.excel_processor.ooxml import OoxmlError, XlsxPackage
from conftest import SAMPLE

SAMPLE_BOQ = SAMPLE / "sample_boq.xlsm"


def plans_for(parsed, by_no, wanted: dict[str, list[str]]) -> list[ChildPlan]:
    out = []
    for uno, items in wanted.items():
        row = next(l.row.row for l in parsed.lines if l.row.unique_no == uno)
        for no in items:
            c = by_no[no]
            out.append(ChildPlan(row, c.item_no, c.item_brief, c.cost_sheet_type, c.class_name, c.cost))
    return out


@pytest.fixture(scope="module")
def exported(tmp_path_factory, sample_boq, parsed, indexes):
    out = tmp_path_factory.mktemp("x") / "out.xlsm"
    wanted = {"1.1.1.1": ["CO-21405"], "1.1.2.1": ["CO-21405"], "1.5.1": ["CO-01354", "CO-11786"],
              "1.7.8": ["CO-21711", "CO-19622"]}
    info = export_boq(sample_boq, "Sales BOM", plans_for(parsed, indexes.by_no, wanted), out)
    return out, info, openpyxl.load_workbook(out, data_only=True)["Sales BOM"]


def row_of(ws, uno):
    for r in ws.iter_rows(min_row=3):
        if r[0].value == uno:
            return r
    raise AssertionError(uno)


def test_verification_passes(exported, sample_boq, indexes):
    out, info, _ = exported
    result = verify_export(sample_boq, out, "Sales BOM", info, indexes.by_no)
    assert result["ok"], result["errors"]


def test_column_h_comes_from_each_component_not_the_parent(exported):
    _, _, ws = exported
    assert row_of(ws, "1.5.1")[7].value == "Highside Supply"
    assert row_of(ws, "1.5.1.1")[7].value == "Highside Supply"
    assert row_of(ws, "1.5.1.2")[7].value == "Highside Installation"


def test_column_v_and_d_are_master_values(exported, indexes):
    _, _, ws = exported
    r = row_of(ws, "1.5.1.2")
    assert r[21].value == "CO-11786" and r[3].value == indexes.by_no["CO-11786"].item_brief


def test_class_name_follows_existing_convention(exported):
    _, _, ws = exported
    assert row_of(ws, "1.5.1.1")[20].value == "Component"


def test_child_inherits_parent_description_qty_and_uom(exported):
    _, _, ws = exported
    parent, child = row_of(ws, "1.7.8"), row_of(ws, "1.7.8.1")
    assert child[4].value == parent[4].value
    assert child[8].value == parent[8].value and child[12].value == parent[12].value
    assert child[1].value == "1.7.8" and child[6].value == "N"


def test_new_child_follows_existing_children(exported):
    _, _, ws = exported
    numbers = [r[0].value for r in ws.iter_rows(min_row=3, max_row=12)]
    assert numbers.index("1.1.1.1.2") == numbers.index("1.1.1.1.1") + 1


def test_parent_rows_unchanged(exported, sample_boq):
    _, _, ws = exported
    src = openpyxl.load_workbook(sample_boq, data_only=True)["Sales BOM"]
    for uno in ("1.1.2.1", "1.5.1", "1.7.8"):
        assert [c.value for c in row_of(ws, uno)][:8] == [c.value for c in row_of(src, uno)][:8]


def test_vba_project_is_byte_identical(exported, sample_boq):
    out, _, _ = exported
    h = lambda p: hashlib.sha256(zipfile.ZipFile(p).read("xl/vbaProject.bin")).hexdigest()  # noqa: E731
    assert h(out) == h(sample_boq)


def test_workbook_features_survive(exported):
    out, _, _ = exported
    z = zipfile.ZipFile(out)
    sheet = z.read("xl/worksheets/sheet2.xml").decode()
    for marker in ("<sheetProtection", "<drawing ", "x14:dataValidation", "<conditionalFormatting"):
        assert marker in sheet
    assert b"convertIntoKytesBOQ" in z.read("xl/drawings/drawing1.xml")
    assert "xl/metadata.xml" in z.namelist() and "xl/calcChain.xml" not in z.namelist()
    assert b'fullCalcOnLoad="1"' in z.read("xl/workbook.xml")


def test_formulas_follow_their_rows(exported):
    out, _, _ = exported
    ws = openpyxl.load_workbook(out)["Sales BOM"]
    for row in ws.iter_rows(min_row=3, max_row=80):
        if row[0].value:
            n = row[0].row
            assert row[11].value == f"=IFERROR(J{n}+K{n},0)"
            assert row[19].value == f"=IFERROR(ROUND(O{n}*I{n},2),0)"


def test_filter_range_tracks_inserted_rows(exported):
    out, info, _ = exported
    z = zipfile.ZipFile(out)
    source = zipfile.ZipFile(SAMPLE_BOQ).read("xl/worksheets/sheet2.xml").decode()
    src_end = info["row_mapping"][int(re.search(r'autoFilter ref="A2:X(\d+)"', source).group(1))]
    assert f'A2:X{src_end}'.encode() in z.read("xl/worksheets/sheet2.xml")
    assert f"$A$2:$X${src_end}".encode() in z.read("xl/workbook.xml")


def test_reparse_preserves_hierarchy(exported):
    out, _, _ = exported
    parsed = parse_boq(out)
    line = next(l for l in parsed.lines if l.row.unique_no == "1.5.1")
    assert [c.item_no for c in line.children] == ["CO-01354", "CO-11786"]
    assert not parsed.warnings


def test_verifier_catches_a_wrong_cost_sheet_type(tmp_path, sample_boq, parsed, indexes):
    out = tmp_path / "bad.xlsm"
    p = plans_for(parsed, indexes.by_no, {"1.1.2.1": ["CO-21405"]})
    p[0].cost_sheet_type = "Lowside Supply"
    info = export_boq(sample_boq, "Sales BOM", p, out)
    result = verify_export(sample_boq, out, "Sales BOM", info, indexes.by_no, ["Highside Supply", "Highside Installation"])
    assert not result["ok"] and any("Cost Sheet Type" in e for e in result["errors"])


def test_verifier_catches_a_tampered_macro(tmp_path, sample_boq, parsed, indexes):
    out = tmp_path / "ok.xlsm"
    info = export_boq(sample_boq, "Sales BOM", plans_for(parsed, indexes.by_no, {"1.1.2.1": ["CO-21405"]}), out)
    pkg = XlsxPackage(out)
    pkg.parts["xl/vbaProject.bin"] += b"x"
    bad = tmp_path / "tampered.xlsm"
    pkg.save(bad)
    assert not verify_export(sample_boq, bad, "Sales BOM", info, indexes.by_no)["ok"]


def test_source_can_never_be_overwritten(sample_boq):
    with pytest.raises(OoxmlError):
        XlsxPackage(sample_boq).save(sample_boq)


def test_special_characters_are_escaped(tmp_path, sample_boq, parsed):
    row = next(l.row.row for l in parsed.lines if l.row.unique_no == "1.1.2.1")
    out = tmp_path / "amp.xlsm"
    export_boq(sample_boq, "Sales BOM", [ChildPlan(row, "CO-1", 'A & B <"x">', "Highside Supply", "Component", 0)], out)
    ws = openpyxl.load_workbook(out, data_only=True)["Sales BOM"]
    assert row_of(ws, "1.1.2.1.1")[3].value == 'A & B <"x">'


def test_inserted_rows_are_yellow_and_original_rows_keep_their_fill(exported, sample_boq):
    out, info, _ = exported
    ws = openpyxl.load_workbook(out)["Sales BOM"]
    src = openpyxl.load_workbook(sample_boq)["Sales BOM"]
    for e in info["expected"]:
        row = row_of(ws, e["unique_no"])
        assert all(c.fill.fill_type == "solid" and c.fill.fgColor.rgb == "FFFFFF00" for c in row[:22])
    for old, new in list(info["row_mapping"].items())[2:60]:
        a, b = ws.cell(new, 22).fill, src.cell(old, 22).fill
        assert (a.fill_type, a.fgColor.value, a.fgColor.tint) == (b.fill_type, b.fgColor.value, b.fgColor.tint)


def test_yellow_rule_outranks_template_rules_only_on_inserted_rows(exported):
    out, info, _ = exported
    sheet = zipfile.ZipFile(out).read("xl/worksheets/sheet2.xml").decode()
    override = re.search(r'<conditionalFormatting sqref="([^"]+)"><cfRule type="expression" dxfId="\d+" '
                         r'priority="1" stopIfTrue="1">', sheet)
    assert override
    covered = set()
    for part in override.group(1).split():
        a, b = re.fullmatch(r"A(\d+):[A-Z]+(\d+)", part).groups()
        covered |= set(range(int(a), int(b) + 1))
    ws = openpyxl.load_workbook(out, data_only=True)["Sales BOM"]
    assert covered == {row_of(ws, e["unique_no"])[0].row for e in info["expected"]}
    priorities = sorted(map(int, re.findall(r'priority="(\d+)"', sheet)))
    assert priorities[0] == 1 and priorities[1] > 1  # the template's own rules all rank below it


def test_verifier_proves_placement_and_yellow_rows(exported, sample_boq, indexes):
    out, info, _ = exported
    result = verify_export(sample_boq, out, "Sales BOM", info, indexes.by_no)
    assert result["checks"]["placement_verified"] == 4  # one block per parent line
    assert result["checks"]["inserted_rows_yellow"] == 6  # every inserted row, across the row


def test_verifier_catches_an_inserted_row_without_yellow(tmp_path, exported, sample_boq, indexes):
    from openpyxl.styles import PatternFill
    out, info, _ = exported
    wb = openpyxl.load_workbook(out, keep_vba=True)
    ws = wb["Sales BOM"]
    inserted = next(r for r in range(3, ws.max_row + 1) if ws.cell(r, 22).value == "CO-01354"
                    and ws.cell(r, 1).fill.fgColor.rgb == "FFFFFF00")
    ws.cell(inserted, 3).fill = PatternFill(fill_type=None)
    bad = tmp_path / "unyellow.xlsm"
    wb.save(bad)
    result = verify_export(sample_boq, bad, "Sales BOM", info, indexes.by_no)
    assert not result["ok"] and any("not yellow" in e for e in result["errors"])


def test_verifier_catches_a_component_under_the_wrong_parent(tmp_path, exported, sample_boq, indexes):
    out, info, _ = exported
    moved = dict(info)
    # Claim the first block's components belong one row lower than they were written.
    moved["blocks"] = [dict(b) for b in info["blocks"]]
    moved["blocks"][0]["block_end"] += 1
    result = verify_export(sample_boq, out, "Sales BOM", moved, indexes.by_no)
    assert not result["ok"]
