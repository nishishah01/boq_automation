"""Regression tests for the matching-accuracy fixes, built from real BOQ wording and real master records."""
import pytest

from app.services.ai_matching.engine import MODEL_NOT_FOUND, MatchConfig, MatchingEngine
from app.services.ai_matching.requirements import build_requirement, precedent_key, split_clauses
from app.services.boq_parser.parser import BoqRow, SalesLine
from app.services.item_master.normalize import canon, extract_attrs, forms_conflict

HS = ["Highside Supply", "Highside Installation"]
CASSETTE_1WAY = ("Supply of VRV indoor One way Cassette units of the following capacities including all "
                 "accessories, supports, return air grille panel, in built drain pump etc. as per specifications")


def make_line(text: str, *sections: str, cst: str = "Highside Supply") -> SalesLine:
    row = lambda uno, t, y, h: BoqRow(1, uno, "", "", t, t, y, h, 1, "NOS", 0, "", "")  # noqa: E731
    return SalesLine(row("1.1.1", text, "Y", cst), [row(f"1.{i}", s, "N", "Section") for i, s in enumerate(sections)])


def picked(engine, line) -> list[str]:
    return [s.component.item_no for s in engine.match_line(line).selections]


def test_model_line_under_an_including_heading_finds_its_model(engine):
    # The original failure: the line's own text was lost in the "including ..." clause.
    assert picked(engine, make_line("2.01 TR FXKQ63ARV16", CASSETTE_1WAY)) == ["CO-01383"]
    assert picked(engine, make_line("1.59 TR FXKQ50ARV16", CASSETTE_1WAY)) == ["CO-01378"]


def test_exact_model_beats_a_closer_worded_generic_item(engine):
    r = engine.match_line(make_line("2.01 TR FXKQ63ARV16", CASSETTE_1WAY))
    generic = [c for c in r.candidates if c["item_no"] == "CO-16799"]  # 4 WAY CASSETTE UNIT 2.02 TR
    assert generic and generic[0]["decision"] == "rejected"


def test_model_written_with_spaces_or_hyphens(engine):
    for text in ("1.02 TR FXAQ 32 ARV16", "1.02 TR FXAQ-32-ARV16"):
        assert picked(engine, make_line(text, "Supply of VRV indoor Hi-wall units")) == ["CO-21406"]


def test_missing_model_is_never_silently_substituted(engine):
    # FXKQ40ARV16 exists only under Lowside Supply in the real master.
    r = engine.match_line(make_line("1.28 TR FXKQ40ARV16", CASSETTE_1WAY))
    for s in r.selections:
        assert "FXKQ" not in s.component.item_brief or "FXKQ40ARV16" in s.component.item_brief
        assert any(f.startswith("Exact model FXKQ40ARV16 is not in the Item Master") for f in s.flags)


def test_one_way_and_four_way_cassettes_are_different_equipment():
    assert forms_conflict({"CASSETTE_1WAY"}, {"CASSETTE_4WAY"})
    assert not forms_conflict({"CASSETTE_1WAY"}, {"CASSETTE"})
    assert not forms_conflict({"CASSETTE_1WAY"}, set())


def test_equipment_type_conflict_rejects_without_a_model(engine):
    r = engine.match_line(make_line("One way cassette unit 2.02 TR", "Supply of VRV indoor units"))
    assert "CO-16799" not in [s.component.item_no for s in r.selections]


def test_indoor_and_outdoor_are_not_interchanged(engine):
    r = engine.match_line(make_line("Outdoor unit 4 TR", "Supply of DX units"))
    assert all("IDU" not in s.component.canon_brief.split() for s in r.selections)


def test_included_items_are_not_added(engine):
    r = engine.match_line(make_line("11.0 Tr - FDR130FRV16 Remote Controller Included",
                                    "DX Ductable machine (High Static) - Daikin"))
    assert [s.component.item_no for s in r.selections] == ["CO-01185"]
    assert any(c["scope"].upper().startswith("REMOTE") for c in r.candidates)  # still shown


def test_fixing_reference_is_not_a_second_component():
    head, extras = split_clauses(canon("Control cable of 2 C + 1 Sq.mm cable shall be properly clamped "
                                       "along with Refrigerant Piping to ceiling"))
    assert extras == []


def test_clause_sizes_stay_with_their_clause():
    line = make_line("SITC - Refrigerant Piping: copper refrigerant piping to interconnect outdoor and indoor "
                     "units complete with all accessories, with 19 mm /13 mm sleeves.")
    req = build_requirement(line)
    assert not req.attrs.sizes  # 19 and 13 mm belong to the sleeves, not the pipe
    sleeves = [i for i in req.scope_items if "SLEEVE" in i.query]
    assert sleeves and {19.0, 13.0} <= sleeves[0].attrs.sizes


@pytest.mark.parametrize("text,check", [
    ("Supply & Installastion Copper Pipe", lambda t, a: "INSTALLATION" in t),
    ("INSTALLTION OF LAG COATING", lambda t, a: "INSTALLATION" in t),
    ("Dismantle Charges", lambda t, a: "DISMANTLING" in t),
    ("DGT JOINT FOR ODUs", lambda t, a: "GAS TIGHT JOINT" in t),
    ("VRV ALPHA", lambda t, a: "ALFA" in t),
    ("TOPPING UP OF REFRIGERANT GAS - R-410A", lambda t, a: a.refrigerants == {"R410A"}),
    ("Control Cabling of 2 C + 1 Sq.mm.", lambda t, a: a.core == {2.0} and a.sqmm == {1.0}),
])
def test_terminology(text, check):
    assert check(canon(text), extract_attrs(text))


def test_precedent_adds_weight_and_a_rejection_sticks(indexes):
    line = make_line("Ductable Unit 4 TR", "Supply of DX units")
    key = precedent_key(line.row.text, line.context)
    wrong = MatchingEngine(indexes, MatchConfig(HS, precedents={key: {"CO-16807": -1}}))
    r = wrong.match_line(line)
    assert "CO-16807" not in [s.component.item_no for s in r.selections]
    right = MatchingEngine(indexes, MatchConfig(HS, precedents={key: {"CO-16807": 1}}))
    s = right.match_line(line).selections[0]
    assert s.component.item_no == "CO-16807" and "Approved by an engineer" in s.reason


def test_precedent_never_overrides_a_technical_conflict(indexes):
    line = make_line("Ductable Unit 4 TR", "Supply of DX units")
    key = precedent_key(line.row.text, line.context)
    e = MatchingEngine(indexes, MatchConfig(HS, precedents={key: {"CO-16804": 1}}))  # an 8 TR unit
    assert "CO-16804" not in [s.component.item_no for s in e.match_line(line).selections]


def test_model_found_in_description_column(indexes):
    # A record whose model is only in Description must still be found by model.
    c = indexes.by_no["CO-01383"]
    assert "FXKQ63ARV16" in c.attrs.models


def test_model_not_found_message_is_a_review_flag():
    from app.services.jobs import initial_status
    flag = MODEL_NOT_FOUND.format(models="X1", target="Highside Supply")
    assert initial_status(90, [flag], "Explicit", 75, True) == "review"
