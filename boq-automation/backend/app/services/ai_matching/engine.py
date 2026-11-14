"""The matching pipeline: requirement > filter > retrieve > score > decide > validate."""
import threading
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
from .scorer import STRONG_HISTORY, Scored, history_strength, score_candidate

AI_UNAVAILABLE_FLAG = "AI matching was unavailable, so this was chosen by rule scoring"
MODEL_NOT_FOUND = ("Exact model {models} is not in the Item Master under {target}; "
                   "this is the closest alternative, for review only")
MIN_SELECT_SCORE = 45  # below this a scope item is reported unmatched, not guessed
# The master has few installation items, so near-misses are common there; a gap is safer.
MIN_INSTALL_SCORE = 60
TIE_MARGIN = 3
# A line the engine believes is Lowside (or other) scope is flagged, never given Highside items.
OTHER_SCOPE_SHARE = 0.6  # of similar verified descriptions mapped only outside the enabled types
OTHER_SCOPE_SCORE = 70  # best Item Master score outside the enabled types...
OTHER_SCOPE_MARGIN = 15  # ...beating the best enabled-type score by this much
COMPANION_SUPPORT = 0.5  # share of similar verified descriptions that must include an extra component
COMPANION_LABEL = "Used with similar verified BOQs"
SCOPE_REVIEW = "scope_review"


@dataclass
class MatchConfig:
    selected_types: list[str]
    max_candidates: int = 5
    suggest_inferred: bool = True
    mode: str = "mock"  # mock | claude | openai | gemini
    # Engineers' earlier decisions: precedent_key -> {item_no: +1 approved / -1 marked wrong}
    precedents: dict = field(default_factory=dict)
    # knowledge.index.KnowledgeSnapshot: verified historical BOQs and approved reviews
    knowledge: object = None


@dataclass
class Selection:
    component: Component
    confidence: int
    selection_type: str
    reason: str
    scope_label: str
    flags: list[str] = field(default_factory=list)
    source: str = "rule"
    classification: object = None  # knowledge.index.Classification, set by the validator
    evidence: list[dict] = field(default_factory=list)  # verified examples supporting the choice


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
    scope_note: str = ""  # set when the line looks like Lowside or other unsupported scope
    evidence: object = None  # knowledge.index.LineEvidence
    history: list[dict] = field(default_factory=list)  # similar verified descriptions, for review

    @property
    def status(self) -> str:
        if not self.in_scope:
            return "skipped"
        if self.scope_note:
            return SCOPE_REVIEW
        if self.selections:
            return "partial" if self.unmatched else "matched"
        return "unmatched"


def line_scope(line: SalesLine, req: Requirement, selected: list[str]) -> tuple[list[str], str]:
    """Which Cost Sheet Types to search for this line, or why it is out of scope."""
    if not line.row.cost_sheet_type:
        # Other BOQ formats rarely state a Cost Sheet Type: search every selected type that fits
        # the line's activities; match_line keeps the group whose items fit best.
        targets = [t for t in selected if t in REGISTRY and REGISTRY[t].activity in req.activities]
        if not targets:
            return [], f"Line scope ({', '.join(req.activities)}) has no selected Cost Sheet Type"
        return targets, ""
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
    """Safe to share between threads: match_line only reads the indexes and the config."""

    def __init__(self, indexes: MasterIndexes, config: MatchConfig, ai_client=None, ai_failure_limit: int = 0):
        self.indexes = indexes
        self.config = config
        self.ai_client = ai_client
        # 0 = never give up on the provider. Otherwise, after this many failures in a row the
        # remaining lines skip the call (each would only wait and fail the same way).
        self.ai_failure_limit = ai_failure_limit
        self._ai_failures = 0
        self._ai_lock = threading.Lock()

    def _evaluate(self, payload: dict):
        with self._ai_lock:
            if self.ai_failure_limit and self._ai_failures >= self.ai_failure_limit:
                raise AIUnavailable(f"AI matching skipped after {self._ai_failures} failed calls in a row")
        try:
            answer = providers.evaluate(self.config.mode, payload, self.ai_client)  # the one real call
        except AIUnavailable:
            with self._ai_lock:
                self._ai_failures += 1
            raise
        with self._ai_lock:
            self._ai_failures = 0
        return answer

    def _shortlist(self, item: ScopeItem, req: Requirement, target: str,
                   precedent: dict | None = None, evidence=None) -> ScopeOutcome:
        """Several independent retrievals, merged by Item No., then scored on one scale."""
        attrs = item.attrs or req.attrs
        index = self.indexes.index(target)
        precedent = precedent or {}
        history = evidence.items if evidence is not None else {}
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
        for c in index.by_item_nos({n for n, v in precedent.items() if v > 0} | set(history)):
            add(c, found.get(c.item_no, (c, 0.4))[1])  # verified or approved before: always considered
        scored = [score_candidate(item, attrs, c, sim, precedent.get(c.item_no, 0), history.get(c.item_no))
                  for c, sim in found.values()]
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

    def _evidence(self, line: SalesLine, req: Requirement):
        kb = self.config.knowledge
        if kb is None or kb.empty:
            return None
        return kb.evidence(line.row.text, line.context, req.attrs, set(self.indexes.by_no),
                           self.config.selected_types)

    def match_line(self, line: SalesLine) -> LineResult:
        req = build_requirement(line)
        self._resolve_spaced_models(line, req)
        precedent = self.config.precedents.get(precedent_key(line.row.text, line.context), {})
        targets, why = line_scope(line, req, self.config.selected_types)
        if not targets:
            return LineResult(req, in_scope=False, skip_reason=why)
        evidence = self._evidence(line, req)
        result = LineResult(req, in_scope=True, evidence=evidence,
                            history=evidence.payload() if evidence is not None else [])
        groups = list(dict.fromkeys(REGISTRY[t].group for t in targets))
        best: tuple[float, str, list[ScopeOutcome]] | None = None
        for group in groups:
            outs = []
            for item in req.scope_items:
                target = next((t for t in targets if REGISTRY[t].group == group and REGISTRY[t].activity == item.activity), None)
                if target is None:
                    continue
                if item.selection_type == "Inferred" and not self.config.suggest_inferred:
                    continue
                outs.append(self._shortlist(item, req, target, precedent, evidence))
            strength = sum(max((x.score for x in o.candidates if not x.rejected), default=0) for o in outs)
            if best is None or strength > best[0]:
                best = (strength, group, outs)
        outcomes = best[2] if best else []
        if len(groups) > 1 and best:
            result.notes.append(f"This BOQ line has no Cost Sheet Type; {best[1]} items fitted it best.")

        outcomes += self._companions(req, evidence, outcomes)
        self._note_blank_cost_sheet(req, result, outcomes)
        result.candidates = self._candidate_payload(outcomes)
        if not line.row.cost_sheet_type:
            self._check_scope(req, outcomes, evidence, result)
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
        if result.scope_note:
            # Never put a Highside component on what looks like Lowside work: leave it to an engineer.
            if result.selections:
                result.notes.append("Suggestions withheld for review: " + "; ".join(
                    f"{s.component.item_no} {s.component.item_brief}" for s in result.selections[:4]))
            result.selections = []
        validator.finalize_line(result, self.indexes, line, self.config.selected_types,
                                self.config.knowledge, evidence)
        return result

    def _companions(self, req: Requirement, evidence, outcomes: list[ScopeOutcome]) -> list[ScopeOutcome]:
        """Components that similar verified BOQs consistently include but no scope item asked for
        (an installation item for an equipment line, for example). Suggested for review only."""
        if evidence is None or not req.scope_items:
            return []
        covered = {s.component.item_no for o in outcomes for s in o.candidates}
        head = next((i for i in req.scope_items if i.is_head), req.scope_items[0])
        extra = []
        for no, ev in evidence.items.items():
            c = self.indexes.by_no.get(no)
            if c is None or no in covered or c.cost_sheet_type not in self.config.selected_types:
                continue
            if ev.support < COMPANION_SUPPORT or history_strength(ev) < STRONG_HISTORY:
                continue
            activity = REGISTRY[c.cost_sheet_type].activity
            item = ScopeItem(label=COMPANION_LABEL, query=c.item_brief, activity=activity,
                             selection_type="Inferred", head_tokens=sorted(c.token_set),
                             line_tokens=set(head.line_tokens) | c.token_set, attrs=head.attrs, is_head=False)
            scored = score_candidate(item, head.attrs or req.attrs, c, 0.5, 0, ev)
            if not scored.rejected:
                scored.reasons.insert(0, "Not named in the line")
                extra.append(ScopeOutcome(item, c.cost_sheet_type, [scored]))
        return extra

    def _check_scope(self, req: Requirement, outcomes: list[ScopeOutcome], evidence, result: LineResult) -> None:
        """Flag a line that is really Lowside (or other unsupported) work, instead of forcing a match."""
        if evidence is not None and evidence.other_scope_share >= OTHER_SCOPE_SHARE \
                and any(sim >= 0.6 for _, sim, _ in evidence.parents):
            types = sorted({c.scope_type for p, _, _ in evidence.parents for c in p.components
                            if c.scope_type and c.scope_type not in self.config.selected_types})
            result.scope_note = (f"Similar verified BOQ descriptions were mapped to {', '.join(types) or 'other'} "
                                 "components. This looks outside the automated Highside scope; review manually.")
            return
        others = [t for t in REGISTRY if t not in self.config.selected_types and t in self.indexes.by_type]
        heads = [i for i in req.scope_items if i.is_head]
        if not others or not heads:
            return
        best_in = max((x.score for o in outcomes if o.item.is_head for x in o.candidates if not x.rejected),
                      default=0)
        best_out: Scored | None = None
        for item in heads:
            for t in others:
                if REGISTRY[t].activity != item.activity:
                    continue
                for x in self._shortlist(item, req, t).candidates:
                    if not x.rejected and (best_out is None or x.score > best_out.score):
                        best_out = x
        if best_out is not None and best_out.score >= OTHER_SCOPE_SCORE \
                and best_out.score >= best_in + OTHER_SCOPE_MARGIN:
            c = best_out.component
            result.scope_note = (f"The closest Item Master record is {c.cost_sheet_type}: {c.item_no} {c.item_brief} "
                                 f"(score {best_out.score} against {best_in} for Highside items). This looks "
                                 "outside the automated Highside scope; review manually.")

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
                                "historical_uses": s.history.parents if s.history else 0,
                                "reasons": s.reasons, "warnings": s.warnings, "conflicts": s.conflicts})
        return out

    def _decide_with_rules(self, req: Requirement, outcomes: list[ScopeOutcome], result: LineResult) -> None:
        for o in outcomes:
            live = sorted((s for s in o.candidates if not s.rejected), key=lambda s: -s.score)
            ranked, dupes = _canonical_duplicate(live)
            optional = o.item.selection_type == "Inferred"
            companion = o.item.label == COMPANION_LABEL
            if optional and not companion:
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
            if companion:
                reason = f"Verified BOQs include it with this kind of line; suggested for review. {reason}"
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
                                "used_in_verified_boqs": s.history.historical if s.history else 0,
                                "approved_in_reviews": (s.history.parents - s.history.historical) if s.history else 0,
                                "rule_score": s.score, "rule_notes": s.reasons + s.warnings} for s in ranked],
            })
            for s in ranked:
                allowed[s.component.item_no] = (s, o)
        payload = {"sales_line": line.row.text, "parent_sections": line.context,
                   "uom": line.row.uom, "quantity": line.row.billable_qty,
                   "extracted_requirements": req.as_dict(), "suggest_inferred": self.config.suggest_inferred,
                   "historical_evidence": result.history, "rule_scope_concern": result.scope_note or None,
                   "shortlist": shortlist}
        answer = self._evaluate(payload)
        if answer.scope_category in ("lowside", "other") and not result.scope_note:
            why = f": {answer.review_reason}" if answer.review_reason else ""
            result.scope_note = (f"The AI judged this line to be {answer.scope_category} scope{why}. "
                                 "Not mapped automatically; review manually.")
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
