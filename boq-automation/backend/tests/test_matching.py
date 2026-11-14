"""Matching behaviour on real Item Master records."""
import json
import re
from pathlib import Path

import pytest

from app.services.ai_matching import claude_evaluator
from app.services.ai_matching.claude_evaluator import ClaudeLineResult, ClaudeRequirements, ClaudeSelection, AIUnavailable
from app.services.ai_matching.engine import AI_UNAVAILABLE_FLAG, MatchConfig, MatchingEngine
from app.services.ai_matching.requirements import build_requirement
from app.services.item_master.normalize import canon, extract_attrs, extract_models

TRUTH = json.loads((Path(__file__).resolve().parents[2] / "sample_data" / "ground_truth.json").read_text("utf-8"))


def selected(engine, line):
    return [s.component.item_no for s in engine.match_line(line).selections]


def test_cost_sheet_filter_never_crosses(engine, parsed):
    for l in parsed.lines:
        for s in engine.match_line(l).selections:
            assert s.component.cost_sheet_type in ("Highside Supply", "Highside Installation")


def test_supply_only_selection_searches_supply_only(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"]))
    r = e.match_line(line("1.5.1"))
    assert r.selections and all(s.component.cost_sheet_type == "Highside Supply" for s in r.selections)


def test_installation_only_selection(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Installation"]))
    r = e.match_line(line("1.5.1"))
    assert [s.component.item_no for s in r.selections] == ["CO-11786"]


def test_supply_line_skipped_when_only_installation_selected(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Installation"]))
    assert not e.match_line(line("1.1.2.1")).in_scope


def test_lowside_line_is_out_of_scope(engine, line):
    r = engine.match_line(line("1.9.1"))
    assert r.status == "skipped"


def test_exact_model_beats_near_identical_model(engine, line):
    assert selected(engine, line("1.1.2.1")) == ["CO-21405"]


def test_missing_model_is_unmatched_not_guessed(engine, line):
    r = engine.match_line(line("1.1.1.3"))  # FDR130GRV16 is absent; FRV16 and ERV16 exist
    assert not r.selections and r.unmatched


def test_capacity_mismatch_rejected(engine, line):
    assert selected(engine, line("1.3.3")) == []


def test_capacity_match(engine, line):
    assert selected(engine, line("1.3.1")) == ["CO-16804"]


def test_cable_size_match(engine, line):
    assert selected(engine, line("1.7.1")) == ["CO-16773"]
    assert selected(engine, line("1.7.2")) == ["CO-16772"]


def test_size_match(engine, line):
    assert selected(engine, line("1.7.6")) == ["CO-18004"]


def test_multi_component_with_explicit_insulation(engine, line):
    got = selected(engine, line("1.7.8"))
    assert "CO-21711" in got and len(got) == 2


def test_single_component_line_gets_one(engine, line):
    assert len(selected(engine, line("1.4.2"))) == 1


def test_combined_scope_selects_supply_and_installation(engine, line):
    r = engine.match_line(line("1.5.2"))
    assert {s.component.cost_sheet_type for s in r.selections} == {"Highside Supply", "Highside Installation"}


def test_no_item_no_is_invented(engine, parsed, indexes):
    for l in parsed.lines:
        for s in engine.match_line(l).selections:
            assert s.component.item_no in indexes.by_no


def test_no_duplicates_within_a_line(engine, parsed):
    for l in parsed.lines:
        got = [s.component.item_no for s in engine.match_line(l).selections]
        assert len(got) == len(set(got))


def test_existing_child_is_not_added_again(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"]))
    r = e.match_line(line("1.1.1.1"))
    assert "CO-01185" not in [s.component.item_no for s in r.selections]


def test_included_accessory_is_never_explicit(indexes, line):
    req = build_requirement(line("1.1.1.1"))
    inc = [i for i in req.scope_items if "REMOTE" in i.query.upper()]
    assert inc and all(i.selection_type == "Inferred" for i in inc)


def test_inferred_suggestions_can_be_switched_off(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"], suggest_inferred=False))
    l = line("1.1.1.1")
    children, l.children = l.children, []
    try:
        assert all(s.selection_type == "Explicit" for s in e.match_line(l).selections)
    finally:
        l.children = children


def test_duplicate_master_records_are_flagged(engine, line):
    r = engine.match_line(line("1.1.3.1"))
    assert r.selections
    assert any("other Item Nos" in f or "alternative" in f for s in r.selections for f in s.flags)


def test_master_misclassification_is_flagged(engine, line):
    r = engine.match_line(line("1.6.3"))
    assert r.selections and any("worded as equipment" in f for s in r.selections for f in s.flags)


def _line(text, section, cst="Highside Supply"):
    from app.services.boq_parser.parser import BoqRow, SalesLine
    row = lambda uno, t, y, h: BoqRow(1, uno, "", "", t, t, y, h, 1, "NOS", 0, "", "")  # noqa: E731
    return SalesLine(row("1.1.1", text, "Y", cst), [row("1.1", section, "N", "Section")])


def test_module_sum_is_one_unit_not_two_components(engine):
    # "24 + 14 HP" is how a 38 HP outdoor unit's modules are written.
    r = engine.match_line(_line("38 HP - 24 + 14 HP RXQ38BRY16", "Top Discharge ODU"))
    assert [s.component.item_no for s in r.selections] == ["CO-21405"]
    assert not r.unmatched and r.status == "matched"


def test_plus_between_words_still_splits(line):
    req = build_requirement(_line("Ductable Unit 8 TR + Remote Controller", "Supply of units"))
    assert len([i for i in req.scope_items if i.activity == "supply"]) == 2


def test_section_context_supplies_the_subject(line):
    req = build_requirement(line("1.1.1.2"))
    assert "FDMF30CRV16" in req.attrs.models and req.activities == ["supply"]


def test_spec_row_inherits_from_section(line):
    req = build_requirement(line("1.9.1"))
    assert 40.0 in req.attrs.sizes and "PVC" in req.attrs.materials
    assert set(req.activities) == {"supply", "installation"}
    assert any("Insulation" in a.title() for a in req.additional)


@pytest.mark.parametrize("text,key,value", [
    ("DUCTABLE UNIT 8 TR", "tr", 8), ("OUTDOOR UNIT 30.14 TR", "tr", 30.14), ("AHU 10,000 CFM", "cfm", 10000),
    ("DAIKIN RXQ20BRY16 ODU 20 HP", "hp", 20), ("STARTER PANEL 1.5 KW", "kw", 1.5),
])
def test_capacity_parsing(text, key, value):
    assert extract_attrs(text).capacity[key] == value


@pytest.mark.parametrize("text,size", [("PVC DRAIN PIPE 40 MM", 40), ("Installation Ø9.5mm ( 3/8 )", 9.5), ("JET NOZZLE 400", 400),
                                       ("BALL VALVE DN 50", 50)])
def test_size_parsing(text, size):
    a = extract_attrs(text)
    assert size in a.sizes | a.soft_sizes


def test_model_extraction_ignores_units():
    assert extract_models("R410A 40MM IP54 FDR130FRV16") == {"FDR130FRV16"}
    assert extract_models("MS-GK18VA") == {"MSGK18VA"}


def test_typo_normalisation():
    assert "INSTALLATION" in canon("INSTALATION OF MITSUBISHI TOWER UNIT")
    assert canon("Air Handlng Unit") == "AHU"


class FakeClaude:
    """Stands in for the API. The real call has not been exercised against live Claude."""
    def __init__(self, answer):
        self.answer = answer
        self.payloads = []

    def __call__(self, payload, client=None):
        self.payloads.append(payload)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _answer(selections, **kw):
    return ClaudeLineResult(requirements=ClaudeRequirements(equipment_or_component="x", activities=["supply"],
                                                            additional_requirements=[]),
                            selections=selections, unresolved=kw.get("unresolved", []),
                            needs_review=kw.get("needs_review", False), review_reason=kw.get("review_reason"))


def test_claude_invented_item_no_is_discarded(indexes, line, monkeypatch):
    fake = FakeClaude(_answer([ClaudeSelection(item_no="CO-99999", selection_type="Explicit", confidence=99, reason="x")]))
    monkeypatch.setattr(claude_evaluator, "evaluate", fake)
    r = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="claude")).match_line(line("1.1.2.1"))
    assert not r.selections and any("not shortlisted" in n for n in r.notes)


def test_claude_selection_uses_master_values(indexes, line, monkeypatch):
    fake = FakeClaude(_answer([ClaudeSelection(item_no="CO-21405", selection_type="Explicit", confidence=91, reason="Model")]))
    monkeypatch.setattr(claude_evaluator, "evaluate", fake)
    r = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="claude")).match_line(line("1.1.2.1"))
    s = r.selections[0]
    assert (s.component.cost_sheet_type, s.confidence, s.source) == ("Highside Supply", 91, "claude")
    assert len(json.dumps(fake.payloads[0])) < 20000  # a shortlist, never the master


def test_claude_failure_degrades_to_rules_and_forces_review(indexes, line, monkeypatch):
    monkeypatch.setattr(claude_evaluator, "evaluate", FakeClaude(AIUnavailable("Claude rate limited")))
    r = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="claude")).match_line(line("1.1.2.1"))
    assert r.engine == "rule" and r.selections[0].component.item_no == "CO-21405"
    assert AI_UNAVAILABLE_FLAG in r.selections[0].flags


def test_ground_truth_uses_real_records(indexes):
    for t in TRUTH["lines"]:
        for group in t["expected"]:
            for e in group:
                assert indexes.by_no[e["item_no"]].item_brief == e["item_brief"]
                assert re.fullmatch(r"CO-\d+", e["item_no"])
