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
    result = verify_export(sample_boq, out, "Sales BOM", info, indexes.by_no)
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
