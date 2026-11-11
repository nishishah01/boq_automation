"""Measure matching against engineers' real BOQs (their chosen components are the answers).

For each workbook, every sales line's existing children are hidden from the engine. Only
Component (CO-) children that exist in the Item Master can be scored. Installation (IN-)
items are reported separately: they are not in the supplied master.

Two questions are measured separately, because they fail for different reasons:
  retrieval  was the right Item No. among the candidates the engine considered?
  selection  did the engine then choose it, and nothing wrong?

    python scripts/evaluate_real.py --item-master <ITEM MASTER.xlsx> --boq <a.xlsm> --boq <b.xlsm>
        [--types highside|all] [--mode mock|claude|openai|gemini] [--app-root <folder containing app/>]
        [--json out.json]
"""
import argparse
import json
import sys
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item-master", required=True)
    ap.add_argument("--boq", action="append", required=True)
    ap.add_argument("--types", default="all", choices=["highside", "all"])
    ap.add_argument("--mode", default="mock")
    ap.add_argument("--app-root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--json")
    args = ap.parse_args()
    sys.path.insert(0, args.app_root)

    from app.core.cost_sheets import REGISTRY
    from app.services.ai_matching import engine as engine_mod
    from app.services.ai_matching.retrieval import MasterIndexes
    from app.services.boq_parser.parser import parse_boq
    from app.services.item_master.processor import read_item_master

    types = ["Highside Supply", "Highside Installation"] if args.types == "highside" else list(REGISTRY)
    t0 = time.time()
    master = read_item_master(args.item_master)
    by_no = {c.item_no: c for c in master}
    eng = engine_mod.MatchingEngine(MasterIndexes(master), engine_mod.MatchConfig(types, mode=args.mode))

    retrieved: list[set] = []
    original = eng._shortlist

    def recording(item, req, target, *rest):
        out = original(item, req, target, *rest)
        retrieved[-1].update(s.component.item_no for s in out.candidates)
        return out
    eng._shortlist = recording

    rows = []
    for path in args.boq:
        for line in parse_boq(path).lines:
            if line.row.cost_sheet_type not in types:
                continue
            gold_all = [c.item_no for c in line.children]
            gold = [g for g in gold_all if g in by_no and by_no[g].cost_sheet_type in types]
            if not gold_all:
                continue
            line.children = []
            retrieved.append(set())
            r = eng.match_line(line)
            got = [s.component.item_no for s in r.selections]
            rows.append({"boq": Path(path).name, "line": line.row.unique_no, "text": line.row.text[:70],
                         "cost_sheet_type": line.row.cost_sheet_type, "gold": gold,
                         "gold_not_in_master": [g for g in gold_all if g not in by_no],
                         "gold_other_type": [g for g in gold_all if g in by_no and g not in gold],
                         "retrieved": sorted(g for g in gold if g in retrieved[-1]),
                         "selected": got, "engine": r.engine,
                         "model_line": bool(r.requirement.attrs.models)})

    tp = sum(len(set(r["selected"]) & set(r["gold"])) for r in rows)
    sel = sum(len(r["selected"]) for r in rows)
    gold_n = sum(len(r["gold"]) for r in rows)
    ret = sum(len(r["retrieved"]) for r in rows)
    lines_scored = [r for r in rows if r["gold"]]
    exact_lines = sum(1 for r in lines_scored if set(r["selected"]) == set(r["gold"]))
    model_rows = [r for r in lines_scored if r["model_line"]]
    summary = {
        "lines": len(rows), "lines_with_scorable_gold": len(lines_scored),
        "gold_components": gold_n, "selected": sel, "correct": tp,
        "retrieval_recall": ret / gold_n if gold_n else 0,
        "selection_given_retrieved": tp / ret if ret else 0,
        "precision": tp / sel if sel else 0, "recall": tp / gold_n if gold_n else 0,
        "false_positives": sel - tp, "missed": gold_n - tp,
        "line_exact_accuracy": exact_lines / len(lines_scored) if lines_scored else 0,
        "model_lines": len(model_rows),
        "model_line_accuracy": (sum(1 for r in model_rows if set(r["selected"]) == set(r["gold"])) / len(model_rows))
        if model_rows else 0,
        "unscorable_IN_items": sum(len(r["gold_not_in_master"]) for r in rows),
        "seconds": round(time.time() - t0),
    }
    out = {"summary": summary, "rows": rows}
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    for r in rows:
        if r["gold"] and set(r["selected"]) != set(r["gold"]):
            print(f"  MISS {r['boq'][:28]:28} {r['line']:13} want {r['gold']} retrieved {r['retrieved']} got {r['selected']} | {r['text']}")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
