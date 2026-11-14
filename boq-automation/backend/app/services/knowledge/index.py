"""Searchable knowledge from verified historical BOQs and approved review decisions.

Priority, highest first:
  1. historical  mappings extracted from uploaded, verified BOQs
  2. feedback    mappings engineers approved or corrected in this app's review
  3. the Item Master's own record
  4. AI / rule inference (the matching engine itself)

Retrieval never trusts wording alone: a past BOQ description only counts as evidence when its
technical attributes (model, capacity, size, material, equipment type...) are compatible with
the new line. Item Numbers from the knowledge base are used only when they exist in the Item
Master the run uses, so nothing can be invented from history.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from app.services.ai_matching.retrieval import HybridTextIndex
from app.services.item_master.normalize import MATERIAL_FAMILIES, Attrs, canon, extract_attrs, forms_conflict

SOURCE_WEIGHT = {"historical": 1.0, "feedback": 0.7}
MIN_SIMILARITY = 0.42  # below this a past description is not considered similar
EXACT = 0.97
CLEAR_MAJORITY = 0.75  # share of uses a classification needs before it is applied without review
DOC_CHARS = 600  # characters of a past description used for text search
INDEX_DIMS = 128  # embedding size of the knowledge index (the Item Master index uses 256)


@dataclass
class KnownComponent:
    item_no: str
    item_brief: str
    cost_sheet_type: str  # as written in the verified BOQ ("" = left blank there)
    class_name: str
    resolved_type: str = ""  # cost_sheet_type, or the Item Master's when the BOQ left it blank

    @property
    def scope_type(self) -> str:
        return self.cost_sheet_type or self.resolved_type


@dataclass
class KnownParent:
    id: int
    source: str  # historical | feedback
    filename: str
    sheet: str
    row: int
    description: str
    context: list[str]
    components: list[KnownComponent]
    attrs: Attrs = field(default_factory=Attrs)
    copies: int = 1  # identical mappings found in other historical files (WORKING / FINAL copies)

    @cached_property
    def doc(self) -> str:
        head = self.context[0] if self.context else ""
        # The opening of a long specification carries what it is; the index does not need all of it.
        return canon(f"{self.description[:DOC_CHARS]} {head[:200]}")

    def reference(self, similarity: float) -> dict:
        return {"source": self.source, "file": self.filename, "sheet": self.sheet, "row": self.row,
                "description": self.description, "similarity": round(similarity, 2), "parent_id": self.id,
                "copies": self.copies}


def _close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol * max(a, b)


def compatibility(line: Attrs, past: Attrs) -> tuple[bool, list[str]]:
    """Whether a past description could need the same components; what agrees when it can."""
    agree: list[str] = []
    if line.models and past.models:
        if line.models & past.models:
            agree.append("same model")
        else:
            return False, []
    if forms_conflict(line.forms, past.forms):
        return False, []
    if line.sides and past.sides and not (line.sides & past.sides):
        return False, []
    for key, cap_a in line.capacity.items():
        cap_b = past.capacity.get(key)
        if cap_b is not None:
            if not _close(cap_a, cap_b):
                return False, []
            agree.append(f"same {key.upper()}")
    sa, sb = line.sizes | line.soft_sizes, past.sizes | past.soft_sizes
    if sa and sb:
        if not (sa & sb):
            return False, []
        agree.append("same size")
    if line.dims and past.dims and not (line.dims & past.dims):
        return False, []
    if line.materials and past.materials and not (line.materials & past.materials) \
            and not any(line.materials & f and past.materials & f for f in MATERIAL_FAMILIES):
        return False, []
    if line.refrigerants and past.refrigerants and not (line.refrigerants & past.refrigerants):
        return False, []
    if line.brands and past.brands and not (line.brands & past.brands):
        return False, []
    if line.sqmm and past.sqmm and not (line.sqmm & past.sqmm):
        return False, []
    return True, agree


@dataclass
class ItemEvidence:
    """How verified knowledge supports one Item Master component for one BOQ line."""
    item_no: str
    item_brief: str
    weight: float = 0.0  # summed similarity x source weight of the parents that used it
    support: float = 0.0  # weight / weight of every similar parent: 1.0 = all of them used it
    best_similarity: float = 0.0
    parents: int = 0
    historical: int = 0  # how many of those parents came from verified historical BOQs
    types_historical: Counter = field(default_factory=Counter)
    types_feedback: Counter = field(default_factory=Counter)
    references: list[dict] = field(default_factory=list)

    @property
    def source(self) -> str:
        return "historical" if self.historical else "feedback"

    def summary(self) -> str:
        kind = "verified BOQ" if self.historical else "approved review"
        n = self.historical or self.parents
        return (f"Used for {n} similar {kind} description{'s' if n != 1 else ''} "
                f"(best match {self.best_similarity:.0%})")


@dataclass
class LineEvidence:
    items: dict[str, ItemEvidence] = field(default_factory=dict)
    parents: list[tuple[KnownParent, float, list[str]]] = field(default_factory=list)
    other_scope_share: float = 0.0  # weighted share of similar parents mapped only outside the enabled types

    def payload(self, limit: int = 5) -> list[dict]:
        """Compact examples for the AI prompt and the review screen."""
        return [{**p.reference(sim), "agrees_on": agree,
                 "components": [{"item_no": c.item_no, "item_brief": c.item_brief,
                                 "cost_sheet_type": c.cost_sheet_type} for c in p.components]}
                for p, sim, agree in self.parents[:limit]]


@dataclass
class Classification:
    cost_sheet_type: str
    source: str  # historical | feedback | item_master
    note: str = ""
    conflict: bool = False


def _winner(counter: Counter, prefer: str = "") -> tuple[str, float, str]:
    """Most used type, its share, and a readable tally. A tie goes to `prefer` when it is tied."""
    total = sum(counter.values())
    ranked = counter.most_common()
    top_n = ranked[0][1]
    tied = [t for t, n in ranked if n == top_n]
    choice = prefer if prefer in tied else sorted(tied)[0]
    tally = ", ".join(f"{t} ×{n:g}" for t, n in ranked)
    return choice, counter[choice] / total, tally


class Classifier:
    """Resolve a component's Cost Sheet Type by the priority rule, keeping the master's value."""

    def __init__(self, historical: dict[str, Counter], feedback: dict[str, Counter]):
        self.historical = historical
        self.feedback = feedback

    def overrides(self, master_types: dict[str, str]) -> dict[str, str]:
        """Item No -> type wherever verified knowledge disagrees with the Item Master."""
        out = {}
        for item_no in (self.historical.keys() | self.feedback.keys()) & master_types.keys():
            master_type = master_types[item_no]
            c = self.resolve(item_no, master_type)
            if c.cost_sheet_type and c.cost_sheet_type != master_type:
                out[item_no] = c.cost_sheet_type
        return out

    def resolve(self, item_no: str, master_type: str, local: ItemEvidence | None = None) -> Classification:
        # Similar verified descriptions first: the same component can be classified differently
        # in a different application, and the closest technical context is the best guide.
        if local is not None and local.types_historical:
            t, share, tally = _winner(local.types_historical, master_type)
            if share >= CLEAR_MAJORITY:
                glob = self.historical.get(item_no)
                note = f"Verified BOQs with similar descriptions classify it as {t}"
                if glob and len(glob) > 1:
                    note += f" (all verified uses: {_winner(glob, t)[2]})"
                return Classification(t, "historical", note)
        hist = self.historical.get(item_no)
        if hist:
            t, share, tally = _winner(hist, master_type)
            if share >= CLEAR_MAJORITY:
                note = f"Verified historical BOQs classify it as {t} ({tally})"
                return Classification(t, "historical", note)
            return Classification(t, "historical", f"Verified BOQs disagree on its Cost Sheet Type ({tally}); "
                                                   f"{t} was chosen, confirm before approving", conflict=True)
        fb = (local.types_feedback if local is not None and local.types_feedback else None) or self.feedback.get(item_no)
        if fb:
            t, share, tally = _winner(fb, master_type)
            if share >= CLEAR_MAJORITY:
                return Classification(t, "feedback", f"Approved in earlier reviews as {t} ({tally})")
            return Classification(t, "feedback", f"Earlier reviews disagree on its Cost Sheet Type ({tally}); "
                                                 "confirm before approving", conflict=True)
        return Classification(master_type, "item_master")


class _SubIndex:
    def __init__(self, parents: list[KnownParent]):
        self.parents = parents
        self.text = HybridTextIndex([p.doc for p in parents], INDEX_DIMS) if parents else None
        self.exact: dict[str, list[int]] = {}
        for i, p in enumerate(parents):
            self.exact.setdefault(p.doc, []).append(i)

    def search(self, query: str, k: int) -> list[tuple[KnownParent, float]]:
        if self.text is None:
            return []
        scores = self.text.scores(query, k)
        for i in self.exact.get(canon(query), []):
            scores[i] = 1.0
        order = np.argsort(-scores)[:k]
        return [(self.parents[i], float(scores[i])) for i in order if scores[i] >= MIN_SIMILARITY]


class KnowledgeSnapshot:
    """An immutable, thread-safe view of the knowledge base used by one or more runs."""

    def __init__(self, parents: list[KnownParent], historical_version: int = 0, feedback_version: int = 0,
                 sub_cache: dict | None = None):
        self.parents = parents
        self.version = (historical_version, feedback_version)
        hist = [p for p in parents if p.source == "historical"]
        fb = [p for p in parents if p.source != "historical"]
        cache = sub_cache if sub_cache is not None else {}
        # The historical index changes only on uploads, so it survives many review approvals.
        self._hist = cache.get(("historical", historical_version)) or _SubIndex(hist)
        self._fb = cache.get(("feedback", feedback_version)) or _SubIndex(fb)
        cache.clear()
        cache[("historical", historical_version)] = self._hist
        cache[("feedback", feedback_version)] = self._fb
        tallies: dict[str, dict[str, Counter]] = {"historical": {}, "feedback": {}}
        for p in parents:
            bucket = tallies["historical" if p.source == "historical" else "feedback"]
            for c in p.components:
                if c.cost_sheet_type:
                    bucket.setdefault(c.item_no, Counter())[c.cost_sheet_type] += 1
        self.classifier = Classifier(tallies["historical"], tallies["feedback"])

    @property
    def empty(self) -> bool:
        return not self.parents

    def evidence(self, text: str, context: list[str], attrs: Attrs, known_items: set[str],
                 enabled_types: list[str], k: int = 25) -> LineEvidence:
        """Similar verified descriptions whose specifications are compatible with this line."""
        out = LineEvidence()
        if self.empty:
            return out
        query = canon(f"{text} {context[0] if context else ''}")
        hits = self._hist.search(query, k) + self._fb.search(query, k)
        total = 0.0
        other = 0.0
        for parent, sim in sorted(hits, key=lambda h: -h[1]):
            ok, agree = compatibility(attrs, parent.attrs)
            if not ok:
                continue
            if "same model" in agree:
                sim = min(1.0, sim + 0.1)
            w = sim * SOURCE_WEIGHT.get(parent.source, 0.5)
            total += w
            usable = [c for c in parent.components if c.item_no in known_items]
            if usable and all(c.scope_type and c.scope_type not in enabled_types for c in usable):
                other += w
            out.parents.append((parent, sim, agree))
            for c in usable:
                ev = out.items.setdefault(c.item_no, ItemEvidence(c.item_no, c.item_brief))
                ev.weight += w
                ev.parents += 1
                ev.best_similarity = max(ev.best_similarity, sim)
                if parent.source == "historical":
                    ev.historical += 1
                    if c.cost_sheet_type:
                        ev.types_historical[c.cost_sheet_type] += 1
                elif c.cost_sheet_type:
                    ev.types_feedback[c.cost_sheet_type] += 1
                if len(ev.references) < 3:
                    ev.references.append({**parent.reference(sim), "cost_sheet_type": c.cost_sheet_type})
        for ev in out.items.values():
            ev.support = ev.weight / total if total else 0.0
        out.other_scope_share = other / total if total else 0.0
        return out


def parent_attrs(description: str, context: list[str]) -> Attrs:
    """Technical attributes of a past description, read the same way as a new BOQ line."""
    from app.services.ai_matching.requirements import build_requirement
    from app.services.boq_parser.parser import BoqRow, SalesLine

    def row(text: str) -> BoqRow:
        return BoqRow(0, "", "", "", "", text, "Y", "", 0, "", 0, "", "")
    try:
        return build_requirement(SalesLine(row(description), [row(c) for c in context])).attrs
    except Exception:  # a malformed description must never stop the index from building
        return extract_attrs(description)


class SnapshotCache:
    """Builds snapshots on demand and reuses them until the knowledge base changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: KnowledgeSnapshot | None = None
        self._subs: dict = {}
        self.historical_version = 0
        self.feedback_version = 0

    def bump(self, historical: bool = False) -> None:
        with self._lock:
            if historical:
                self.historical_version += 1
            else:
                self.feedback_version += 1

    def get(self, loader) -> KnowledgeSnapshot:
        with self._lock:
            version = (self.historical_version, self.feedback_version)
            if self._snapshot is None or self._snapshot.version != version:
                self._snapshot = KnowledgeSnapshot(loader(), *version, sub_cache=self._subs)
            return self._snapshot
