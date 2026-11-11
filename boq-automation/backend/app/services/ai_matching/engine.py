"""The matching pipeline: requirement > filter > retrieve > score > decide > validate."""
from dataclasses import dataclass, field

from app.core.cost_sheets import REGISTRY, type_for
from app.services.boq_parser.parser import SalesLine
from app.services.item_master.normalize import canon, joined_models
from app.services.item_master.processor import Component
from app.services.validation import validator

from . import providers
from .claude_evaluator import AIUnavailable
from .requirements import Requirement, ScopeItem, build_requirement, precedent_key
from .retrieval import MasterIndexes
from .scorer import Scored, score_candidate

AI_UNAVAILABLE_FLAG = "AI matching was unavailable, so this was chosen by rule scoring"
MODEL_NOT_FOUND = ("Exact model {models} is not in the Item Master under {target}; "
                   "this is the closest alternative, for review only")
MIN_SELECT_SCORE = 45  # below this a scope item is reported unmatched, not guessed
# The master has few installation items, so near-misses are common there; a gap is safer.
MIN_INSTALL_SCORE = 60
TIE_MARGIN = 3


@dataclass
class MatchConfig:
    selected_types: list[str]
    max_candidates: int = 5
    suggest_inferred: bool = True
    mode: str = "mock"  # mock | claude | openai | gemini
    # Engineers' earlier decisions: precedent_key -> {item_no: +1 approved / -1 marked wrong}
    precedents: dict = field(default_factory=dict)


@dataclass
class Selection:
    component: Component
    confidence: int
    selection_type: str
    reason: str
    scope_label: str
    flags: list[str] = field(default_factory=list)
    source: str = "rule"


@dataclass
class ScopeOutcome:
    item: ScopeItem
    target_type: str
    candidates: list[Scored]


@dataclass
class LineResult:
    requirement: Requirement
    in_scope: bool
    skip_reason: str = ""
    selections: list[Selection] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    engine: str = "rule"

    @property
    def status(self) -> str:
        if not self.in_scope:
            return "skipped"
        if self.selections:
            return "partial" if self.unmatched else "matched"
        return "unmatched"


def line_scope(line: SalesLine, req: Requirement, selected: list[str]) -> tuple[list[str], str]:
    """Which Cost Sheet Types to search for this line, or why it is out of scope."""
    cst = REGISTRY.get(line.row.cost_sheet_type)
    if cst is None or not any(REGISTRY[s].group == cst.group for s in selected if s in REGISTRY):
        return [], f"Cost Sheet Type '{line.row.cost_sheet_type}' is not selected"
    targets = []
    for activity in req.activities:
        t = type_for(cst.group, activity)
        if t in selected:
            targets.append(t)
    if not targets:
        return [], f"Line scope ({', '.join(req.activities)}) has no selected Cost Sheet Type"
    return targets, ""


def _canonical_duplicate(scored: list[Scored]) -> tuple[list[Scored], dict[str, list[str]]]:
    """Identical briefs under several Item Nos: keep the best-populated record, note the rest."""
    groups: dict[str, list[Scored]] = {}
    for s in scored:
        groups.setdefault(s.component.canon_brief, []).append(s)
    kept, dupes = [], {}
    for group in groups.values():
        group.sort(key=lambda s: (-s.score, s.component.cost == 0, not s.component.family, s.component.item_no))
        kept.append(group[0])
        if len(group) > 1:
            dupes[group[0].component.item_no] = [g.component.item_no for g in group[1:]]
    kept.sort(key=lambda s: -s.score)
    return kept, dupes


class MatchingEngine:
    def __init__(self, indexes: MasterIndexes, config: MatchConfig, ai_client=None):
        self.indexes = indexes
        self.config = config
        self.ai_client = ai_client

    def _shortlist(self, item: ScopeItem, req: Requirement, target: str,
                   precedent: dict | None = None) -> ScopeOutcome:
        """Several independent retrievals, merged by Item No., then scored on one scale."""
        attrs = item.attrs or req.attrs
        index = self.indexes.index(target)
        precedent = precedent or {}
        found: dict[str, tuple[Component, float]] = {}

        def add(c: Component, sim: float) -> None:
            if c.item_no not in found or sim > found[c.item_no][1]:
                found[c.item_no] = (c, sim)

        for c, sim in index.search(item.query, k=40):  # the full scope wording
            add(c, sim)
        noun = " ".join(item.head_tokens)
        if noun and noun != canon(item.query):  # the component noun alone, plus its sizes
            sizes = " ".join(f"{s:g} MM" for s in sorted(attrs.sizes | attrs.soft_sizes))
            for c, sim in index.search(f"{noun} {sizes}".strip(), k=25):
                add(c, sim)
        for c in index.by_models(attrs.models):  # exact model, from every master column
            add(c, 1.0)
        for c in index.by_item_nos({n for n, v in precedent.items() if v > 0}):
            add(c, found.get(c.item_no, (c, 0.4))[1])
        scored = [score_candidate(item, attrs, c, sim, precedent.get(c.item_no, 0)) for c, sim in found.values()]
        self._apply_model_gate(attrs.models, target, scored)
        return ScopeOutcome(item, target, scored)

    @staticmethod
    def _apply_model_gate(models: set[str], target: str, scored: list[Scored]) -> None:
        """An exact model outranks every generic item; without one, nothing is chosen silently."""
        if not models:
            return
        names = ", ".join(sorted(models))
        if any(s.model_hit and not s.rejected for s in scored):
            for s in scored:
                if not s.model_hit and not s.rejected:
                    s.conflicts.append(f"Generic item: exact model {names} is in the Item Master")
        else:
            for s in scored:
                if not s.rejected:
                    s.warnings.append(MODEL_NOT_FOUND.format(models=names, target=target))

    def match_line(self, line: SalesLine) -> LineResult:
        req = build_requirement(line)
        self._resolve_spaced_models(line, req)
        precedent = self.config.precedents.get(precedent_key(line.row.text, line.context), {})
        targets, why = line_scope(line, req, self.config.selected_types)
        if not targets:
            return LineResult(req, in_scope=False, skip_reason=why)
        result = LineResult(req, in_scope=True)
        outcomes = []
        for item in req.scope_items:
            target = next((t for t in targets if REGISTRY[t].activity == item.activity), None)
            if target is None:
                continue
            if item.selection_type == "Inferred" and not self.config.suggest_inferred:
                continue
            outcomes.append(self._shortlist(item, req, target, precedent))

        self._note_blank_cost_sheet(req, result, outcomes)
        result.candidates = self._candidate_payload(outcomes)
        if self.config.mode in providers.AI_MODES:
            try:
                self._decide_with_ai(line, req, outcomes, result)
                result.engine = self.config.mode
            except AIUnavailable as e:
                result.notes.append(f"{e}. Fell back to rule scoring; manual review required.")
                self._decide_with_rules(req, outcomes, result)
                for s in result.selections:
                    s.flags.append(AI_UNAVAILABLE_FLAG)
        else:
            self._decide_with_rules(req, outcomes, result)
        validator.finalize_line(result, self.indexes, line, self.config.selected_types)
        return result

    def _resolve_spaced_models(self, line: SalesLine, req: Requirement) -> None:
        """'FXAQ 32 ARV16' or 'FXAQ-32-ARV16' counts as a model only if the master has that model."""
        joined = joined_models(line.row.text)
        found = {m for m in joined if m in self.indexes.models}
        if not found:
            return
        fragments = set().union(*(joined[m] for m in found))
        for item in req.scope_items:
            if item.is_head and item.attrs is not None:
                item.attrs.models = (item.attrs.models - fragments) | found

    def _note_blank_cost_sheet(self, req: Requirement, result: LineResult, outcomes: list[ScopeOutcome]) -> None:
        """Blank-type records are never selected, but a strong one is worth a human's attention."""
        if "" not in self.indexes.by_type or not outcomes:
            return
        o = outcomes[0]
        hits = self.indexes.index("").search(o.item.query, k=10)
        scored = [score_candidate(o.item, req.attrs, c, sim) for c, sim in hits]
        live = [s for s in scored if not s.rejected and s.score >= 70]
        if live:
            best = max(live, key=lambda s: s.score)
            result.notes.append(f"Possible match {best.component.item_no} ({best.component.item_brief}) "
                                "has a blank Cost Sheet Type in the Item Master and needs manual review.")

    def _candidate_payload(self, outcomes: list[ScopeOutcome]) -> list[dict]:
        """What the engine considered: viable candidates, and the nearest rejections with reasons."""
        out = []
        for o in outcomes:
            live = sorted((s for s in o.candidates if not s.rejected), key=lambda s: -s.score)
            ranked, _ = _canonical_duplicate(live)
            dead = sorted((s for s in o.candidates if s.rejected), key=lambda s: (not s.model_hit, -s.score))
            for decision, group in (("candidate", ranked[: self.config.max_candidates]), ("rejected", dead[:3])):
                for s in group:
                    c = s.component
                    out.append({"scope": o.item.label, "activity": o.item.activity, "item_no": c.item_no,
                                "item_brief": c.item_brief, "cost_sheet_type": c.cost_sheet_type,
                                "score": s.score, "decision": decision, "exact_model": s.model_hit,
                                "reasons": s.reasons, "warnings": s.warnings, "conflicts": s.conflicts})
        return out

    def _decide_with_rules(self, req: Requirement, outcomes: list[ScopeOutcome], result: LineResult) -> None:
        for o in outcomes:
            live = sorted((s for s in o.candidates if not s.rejected), key=lambda s: -s.score)
            ranked, dupes = _canonical_duplicate(live)
            optional = o.item.selection_type == "Inferred"
            if optional:
                # "Remote controller included" is in the unit price: listed as a candidate for the
                # engineer, never added on the engine's own judgement.
                continue
            threshold = MIN_INSTALL_SCORE if o.item.activity == "installation" else MIN_SELECT_SCORE
            if not ranked or ranked[0].score < threshold:
                if not optional:
                    near = f" (closest: {ranked[0].component.item_brief}, score {ranked[0].score})" if ranked else ""
                    result.unmatched.append(f"{o.item.label} [{o.target_type}]: no reliable Item Master match{near}")
                continue
            best = ranked[0]
            flags = list(best.warnings)
            if len(ranked) > 1 and ranked[0].score - ranked[1].score <= TIE_MARGIN:
                flags.append(f"Equally relevant alternative: {ranked[1].component.item_no} {ranked[1].component.item_brief}")
            if best.component.item_no in dupes:
                flags.append("Same brief under other Item Nos: " + ", ".join(dupes[best.component.item_no][:4]))
            reason = "; ".join(best.reasons) or "Closest text match"
            if optional:
                reason = f"Line says '{o.item.label} included': suggested for review only. {reason}"
            result.selections.append(Selection(best.component, best.score, o.item.selection_type, reason,
                                               o.item.label, flags))

    def _decide_with_ai(self, line: SalesLine, req: Requirement, outcomes: list[ScopeOutcome],
                            result: LineResult) -> None:
        shortlist, allowed = [], {}
        for o in outcomes:
            live = sorted((s for s in o.candidates if not s.rejected), key=lambda s: -s.score)
            ranked, _ = _canonical_duplicate(live)
            ranked = ranked[:12]
            shortlist.append({
                "scope_item": o.item.label, "activity": o.item.activity, "stated_as": o.item.selection_type,
                "cost_sheet_type": o.target_type,
                "candidates": [{"item_no": s.component.item_no, "item_brief": s.component.item_brief,
                                "description": s.component.description, "exact_model_match": s.model_hit,
                                "family": s.component.family, "uom": s.component.uom,
                                "rule_score": s.score, "rule_notes": s.reasons + s.warnings} for s in ranked],
            })
            for s in ranked:
                allowed[s.component.item_no] = (s, o)
        payload = {"sales_line": line.row.text, "parent_sections": line.context,
                   "uom": line.row.uom, "quantity": line.row.billable_qty,
                   "extracted_requirements": req.as_dict(), "suggest_inferred": self.config.suggest_inferred,
                   "shortlist": shortlist}
        answer = providers.evaluate(self.config.mode, payload, self.ai_client)
        req_extra = answer.requirements.model_dump()
        req.additional = list(dict.fromkeys(req.additional + req_extra.get("additional_requirements", [])))
        result.unmatched.extend(answer.unresolved)
        if answer.needs_review and answer.review_reason:
            result.notes.append(answer.review_reason)
        for sel in answer.selections:
            if sel.item_no not in allowed:
                result.notes.append(f"The AI proposed {sel.item_no}, which was not shortlisted; discarded.")
                continue
            if sel.selection_type == "Inferred" and not self.config.suggest_inferred:
                continue
            scored, o = allowed[sel.item_no]
            flags = list(scored.warnings)
            if answer.needs_review:
                flags.append(answer.review_reason or "The AI requested review")
            result.selections.append(Selection(scored.component, sel.confidence, sel.selection_type, sel.reason,
                                               o.item.label, flags, source=self.config.mode))
