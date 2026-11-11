"""Evaluation report: precision, recall and accuracy against ground truth.

Two sets, reported separately because their evidence differs:
  sample   sample_data/ground_truth.json. Developer-written, not engineer-verified.
  holdout  The real BOQ with its children hidden, so the engineers' own choices are the answer.
           Only Component (CO-) picks can be scored: IN- codes are not in the Item Master.

    python scripts/evaluate.py --sample-dir ../sample_data [--boq <real.xlsm> --item-master <real.xlsx>]
                               [--mode mock|claude] [--out ../docs/EVALUATION.md]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cost_sheets import REGISTRY  # noqa: E402
from app.services.ai_matching.engine import MatchConfig, MatchingEngine  # noqa: E402
from app.services.ai_matching.retrieval import MasterIndexes  # noqa: E402
from app.services.boq_parser.parser import parse_boq  # noqa: E402
from app.services.item_master.processor import read_item_master  # noqa: E402


SAMPLE_NOTE = ("Ground truth written by the developer against real master records; not engineer-verified. "
               "The rules were tuned while building this set, so treat this as a regression check, "
               "not a measure of accuracy.")
HOLDOUT_NOTE = ("Engineers' own CO- selections in the supplied BOQ, hidden from the engine. This is the only "
                "human-verified set, and it is small: use it as a baseline, not a forecast.")


def score(pairs: list[dict]) -> dict:
    """Each pair: {expected: [alternatives per component], selected: [Item Nos], master: {no: cst}}."""
    tp = fp = fn = line_ok = cst_ok = cst_total = 0
    false_pos, missed = [], []
    for p in pairs:
        remaining = list(p["expected"])
        for no in p["selected"]:
            cst_total += 1
            hit = next((g for g in remaining if no in g), None)
            if hit is not None:
                tp += 1
                remaining.remove(hit)
                cst_ok += 1 if p["master"].get(no) in REGISTRY else 0
            else:
                fp += 1
                cst_ok += 1 if p["master"].get(no) in REGISTRY else 0
                false_pos.append(f"{p['id']}: {no} {p['briefs'].get(no, '')}")
        fn += len(remaining)
        missed += [f"{p['id']}: expected {' / '.join(g[:3])}" for g in remaining]
        line_ok += 1 if not remaining and len(p["selected"]) == len(p["expected"]) else 0
    n = len(pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"lines": n, "expected_components": tp + fn, "selected_components": tp + fp, "correct": tp,
            "precision": precision, "recall": recall, "exact_item_no_accuracy": tp / max(tp + fp + fn, 1),
            "line_accuracy": line_ok / max(n, 1), "cost_sheet_type_accuracy": cst_ok / max(cst_total, 1),
            "false_positives": false_pos, "missed": missed}


def run_sample(sample_dir: Path, mode: str) -> dict:
    indexes = MasterIndexes(read_item_master(sample_dir / "sample_item_master.xlsx"))
    truth = {t["unique_no"]: t for t in json.loads((sample_dir / "ground_truth.json").read_text("utf-8"))["lines"]}
    engine = MatchingEngine(indexes, MatchConfig(["Highside Supply", "Highside Installation"], mode=mode))
    pairs = []
    for line in parse_boq(sample_dir / "sample_boq.xlsm").lines:
        t = truth.get(line.row.unique_no)
        if t is None:
            continue
        r = engine.match_line(line)
        pairs.append(_pair(line.row.unique_no, [[e["item_no"] for e in g] for g in t["expected"]], r))
    return score(pairs)


def run_holdout(boq: Path, item_master: Path, mode: str) -> dict:
    indexes = MasterIndexes(read_item_master(item_master))
    engine = MatchingEngine(indexes, MatchConfig(list(REGISTRY), mode=mode))
    pairs = []
    for line in parse_boq(boq).lines:
        gold = [[c.item_no] for c in line.children if c.item_no.startswith("CO-")]
        if line.row.cost_sheet_type not in REGISTRY or not gold:
            continue
        line.children = []
        r = engine.match_line(line)
        pairs.append(_pair(line.row.unique_no, gold, r))
    return score(pairs)


def _pair(identifier: str, expected: list, result) -> dict:
    return {"id": identifier, "expected": expected, "selected": [s.component.item_no for s in result.selections],
            "master": {s.component.item_no: s.component.cost_sheet_type for s in result.selections},
            "briefs": {s.component.item_no: s.component.item_brief for s in result.selections}}


def section(title: str, note: str, m: dict) -> str:
    out = [f"## {title}", "", note, "", "| Metric | Value |", "|---|---|",
           f"| Sales lines | {m['lines']} |", f"| Expected components | {m['expected_components']} |",
           f"| Selected components | {m['selected_components']} |", f"| Correct | {m['correct']} |",
           f"| Precision | {m['precision']:.1%} |", f"| Recall | {m['recall']:.1%} |",
           f"| Exact Item No. accuracy | {m['exact_item_no_accuracy']:.1%} |",
           f"| Parent-description accuracy | {m['line_accuracy']:.1%} |",
           f"| Cost Sheet Type accuracy | {m['cost_sheet_type_accuracy']:.1%} |",
           f"| False positives | {len(m['false_positives'])} |", f"| Missed components | {len(m['missed'])} |", ""]
    if m["false_positives"]:
        out += ["False positives:", ""] + [f"- {x}" for x in m["false_positives"]] + [""]
    if m["missed"]:
        out += ["Missed:", ""] + [f"- {x}" for x in m["missed"]] + [""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-dir", type=Path, required=True)
    ap.add_argument("--boq", type=Path)
    ap.add_argument("--item-master", type=Path)
    ap.add_argument("--mode", default="mock")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    parts = [f"# Matching evaluation (engine mode: {args.mode})", "",
             "Component accuracy here is exact Item No. agreement. An item an engineer would accept but "
             "did not choose counts as wrong. Scores are heuristic until calibrated.", ""]
    sample = run_sample(args.sample_dir, args.mode)
    parts.append(section("Sample set (Highside)", SAMPLE_NOTE, sample))
    if args.boq and args.item_master:
        holdout = run_holdout(args.boq, args.item_master, args.mode)
        parts.append(section("Real BOQ holdout (all four cost sheet types)", HOLDOUT_NOTE, holdout))
    text = "\n".join(parts)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
