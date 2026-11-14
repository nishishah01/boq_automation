"""Stage 5 scoring. Technical compatibility decides; text similarity only ranks what survives.

A hard conflict (wrong model, size, material, make, capacity, equipment form or indoor/outdoor
side) removes a candidate whatever its wording. The score is a deterministic heuristic, not a
calibrated probability.

Priority, highest first: exact model, equipment type, material, size, other specifications,
activity, then wording. When the line has no model, model points simply do not apply.
"""
from dataclasses import dataclass, field

from app.services.item_master.normalize import MATERIAL_FAMILIES, Attrs, forms_conflict
from app.services.item_master.processor import Component

from .requirements import ScopeItem

CAPACITY_UNSTATED_CAP = 44  # just below engine.MIN_SELECT_SCORE
PRECEDENT_BONUS = 12
HISTORY_BONUS = 24  # verified historical / approved use for a similar, compatible description
STRONG_HISTORY = 0.75  # history strength at which verified practice decides among compatible items
# Words that mark an item as equipment, whose price follows its stated capacity.
EQUIPMENT_WORDS = {"UNIT", "AHU", "FCU", "ODU", "IDU", "CHILLER", "FAN", "PUMP", "DUCTABLE", "CASSETTE",
                   "HIWALL", "VRV", "TOWER", "COMPRESSOR", "CONDENSER", "BLOWER", "DEHUMIDIFIER", "HRV",
                   "ERV", "SPLIT", "PACKAGE", "PACKAGED"}


@dataclass
class Scored:
    component: Component
    score: int
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_hit: bool = False
    precedent: int = 0  # +1 approved before for this exact line, -1 marked wrong before
    history: object = None  # knowledge.index.ItemEvidence when verified BOQs used this item for similar lines

    @property
    def rejected(self) -> bool:
        return bool(self.conflicts)


def _close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol * max(a, b)


def _same_family(a: set[str], b: set[str]) -> bool:
    return any(a & f and b & f for f in MATERIAL_FAMILIES)


def is_equipment(c: Component) -> bool:
    a = c.attrs
    return bool(a.models or a.forms or a.sides or a.capacity or (c.token_set & EQUIPMENT_WORDS))


def history_strength(ev) -> float:
    """0..1: how closely and how consistently verified BOQs used this item for such a line."""
    if ev is None:
        return 0.0
    return ev.best_similarity * min(1.0, 0.4 + ev.support)


def score_candidate(item: ScopeItem, req: Attrs, c: Component, sim: float, precedent: int = 0,
                    history=None) -> Scored:
    out = Scored(c, 0, precedent=precedent, history=history)
    has = c.attrs
    points = 0.0
    capacity_unstated = False

    if precedent < 0:
        out.conflicts.append("An engineer marked this wrong for this same BOQ line before")

    # Supply and installation must not be crossed, whatever the Cost Sheet Type says.
    if item.activity == "supply" and c.install_like:
        out.conflicts.append("Worded as installation but searched as supply")

    model_hit = bool(req.models & has.models)
    out.model_hit = model_hit
    if req.models:
        if model_hit:
            points += 40
            out.reasons.append(f"Exact model {', '.join(sorted(req.models & has.models))}")
        elif has.models:
            out.conflicts.append(f"Model {', '.join(sorted(has.models))} differs from required "
                                 f"{', '.join(sorted(req.models))}")
        else:
            points -= 20
            out.warnings.append("Required model is not stated on this item")

    # Equipment type and indoor/outdoor side. An exact model outranks a BOQ's loose wording.
    if not model_hit:
        if forms_conflict(req.forms, has.forms):
            out.conflicts.append(f"Equipment type {', '.join(sorted(has.forms))} differs from required "
                                 f"{', '.join(sorted(req.forms))}")
        elif req.forms and has.forms:
            points += 8
            out.reasons.append("Equipment type " + ", ".join(sorted(req.forms & has.forms) or sorted(has.forms)))
        if req.sides and has.sides and not (req.sides & has.sides):
            out.conflicts.append(f"{'/'.join(sorted(has.sides))} item for an {'/'.join(sorted(req.sides))} requirement")

    if req.brands:
        if req.brands & has.brands:
            points += 6
            out.reasons.append(f"Make {', '.join(sorted(req.brands & has.brands))}")
        elif has.brands:
            out.conflicts.append(f"Make {', '.join(sorted(has.brands))} differs from required {', '.join(sorted(req.brands))}")

    if req.refrigerants and has.refrigerants:
        if req.refrigerants & has.refrigerants:
            points += 10
            out.reasons.append(f"Refrigerant {', '.join(sorted(req.refrigerants & has.refrigerants))}")
        else:
            out.conflicts.append(f"Refrigerant {', '.join(sorted(has.refrigerants))} differs from required "
                                 f"{', '.join(sorted(req.refrigerants))}")

    req_mat = req.materials
    if req_mat and has.materials:
        if req_mat & has.materials:
            points += 8
            out.reasons.append(f"Material {', '.join(sorted(req_mat & has.materials))}")
        elif _same_family(req_mat, has.materials):
            points += 2
            out.warnings.append(f"Material {', '.join(sorted(has.materials))} is close to required {', '.join(sorted(req_mat))}")
        else:
            out.conflicts.append(f"Material {', '.join(sorted(has.materials))} differs from required {', '.join(sorted(req_mat))}")

    req_sizes = req.sizes | req.soft_sizes
    has_sizes = has.sizes | has.soft_sizes
    if req_sizes and not model_hit:
        if req_sizes & has_sizes:
            points += 16
            out.reasons.append(f"Size {max(req_sizes & has_sizes):g} mm")
        elif has_sizes:
            out.conflicts.append(f"Size {sorted(has_sizes)} differs from required {sorted(req_sizes)}")
        else:
            points -= 4
            out.warnings.append("Item does not state a size")

    if req.dims and has.dims and not (req.dims & has.dims):
        out.conflicts.append(f"Dimensions {sorted(has.dims)} differ from required {sorted(req.dims)}")
    elif req.dims and req.dims & has.dims:
        points += 16
        out.reasons.append("Dimensions match")

    equipment = is_equipment(c)
    for key, label in (("tr", "TR"), ("hp", "HP"), ("cfm", "CFM"), ("kw", "kW")):
        if key in req.capacity:
            if key in has.capacity:
                if _close(req.capacity[key], has.capacity[key]):
                    points += 16
                    out.reasons.append(f"Capacity {has.capacity[key]:g} {label}")
                elif not model_hit:
                    out.conflicts.append(f"Capacity {has.capacity[key]:g} {label} differs from required "
                                         f"{req.capacity[key]:g} {label}")
            elif not model_hit and equipment:
                # Gas or piping for a 38 HP system does not state HP; equipment must.
                capacity_unstated = True
                out.warnings.append(f"Item does not state {label}")

    if req.sqmm and has.sqmm:
        if req.sqmm & has.sqmm:
            points += 10
            out.reasons.append("Cable size matches")
        else:
            out.conflicts.append(f"Cable size {sorted(has.sqmm)} differs from required {sorted(req.sqmm)}")
    if req.core and has.core:
        if req.core & has.core:
            points += 6
            out.reasons.append(f"{sorted(req.core & has.core)[0]:g} core")
        else:
            out.conflicts.append(f"Core count {sorted(has.core)} differs from required {sorted(req.core)}")
    if req.gauge and has.gauge:
        if req.gauge & has.gauge:
            points += 8
            out.reasons.append(f"Gauge {sorted(req.gauge & has.gauge)[0]}")
        else:
            out.conflicts.append(f"Gauge {sorted(has.gauge)} differs from required {sorted(req.gauge)}")

    # Component identity runs both ways. Engineers pick short master names ("LINEAR GRILLE")
    # for long BOQ wording, so the item's own words matter more than the BOQ's word count.
    head = [t for t in item.head_tokens if not t[0].isdigit()]
    own = {t for t in c.token_set if not t[0].isdigit()}
    if head and own:
        item_cov = len(own & item.line_tokens) / len(own)
        head_cov = len(own & set(head)) / len(set(head))
        identity = 0.65 * item_cov + 0.35 * head_cov
        points += 46 * (identity if not model_hit else max(identity, 0.7))
        if item_cov == 1:
            points += 3
        named = sorted(own & set(head))
        if named:
            out.reasons.append("Names " + ", ".join(w.title() for w in named[:5]))
        elif not model_hit:
            points -= 20
    if item.activity == "installation" and not c.install_like:
        points -= 15  # validator.component_flags raises the review flag for this
    points += 24 * min(sim * 1.6, 1.0)

    if len(c.token_set) <= 1 and not model_hit:
        points -= 10
        out.warnings.append("Very generic Item Master entry")
    if c.deprecated:
        points -= 25
        out.warnings.append("Marked NA/N-A in the Item Master (retired)")
    if precedent > 0:
        points += PRECEDENT_BONUS
        out.reasons.append("Approved by an engineer for this same BOQ line before")

    strength = history_strength(history)
    if strength:
        points += HISTORY_BONUS * strength
        out.reasons.append(history.summary())
    out.score = max(0, min(99, round(points)))
    if strength >= STRONG_HISTORY and not out.conflicts:
        # Verified practice outranks wording among technically compatible items.
        out.score = max(out.score, min(97, round(70 + 25 * strength)))
    if capacity_unstated and strength < STRONG_HISTORY:
        # Price follows capacity, so a capacity-less record stays a candidate an engineer can
        # pick but is never auto-selected for a line that states one.
        out.score = min(out.score, CAPACITY_UNSTATED_CAP)
    return out
