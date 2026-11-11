"""Stage 1: turn a sales line and its ancestor sections into a structured requirement.

The line's own text always stays in the main scope item: it is what distinguishes one line
from its neighbours ("2.01 TR FXKQ63ARV16" under a shared heading). Only clause markers
("including", "with", "+") split off further components, and each scope item carries its own
attributes, so a size mentioned for sleeves never becomes a size requirement for the pipe.
"""
import re
from dataclasses import dataclass, field

from app.core.cost_sheets import REGISTRY
from app.services.boq_parser.parser import SalesLine
from app.services.item_master.normalize import Attrs, canon, extract_attrs, tokens

CLAUSE_RE = re.compile(
    r"\b(?:ALONG\s*WITH|COMPLETE\s+WITH|COMPLETE\s+IN\s+ALL|INCLUSIVE\s+OF|INCLUDING|INCL\.?|WITH(?!OUT))\b"
    r"|(?<![\d.])\s\+\s(?![\d.])|;")
INCLUDED_RE = re.compile(r"([A-Z][A-Z ]{2,40}?)\s+INCLUDED\b")
SUPPLY_RE = re.compile(r"\bSUPPLY(ING)?\b|\bPROVIDING\b|\bFURNISHING\b|\bPROCUREMENT\b")
INSTALL_RE = re.compile(r"\bINSTALLATION\b")
# Clause wording that describes workmanship, not a selectable component.
PROCESS_RE = re.compile(
    r"\b(CLAMPS?|SUPPORTS?|HANGERS?|PAINT|PRIMER|COATS?|TESTING|VACCUM\w*|VACUUM\w*|CHARGING|WORKS?|"
    r"LABOU?RS?|HELP|ETC|ACCESSORIES|HARDWARE|CONSUMABLES?)\b")
# "...shall be properly clamped along with refrigerant piping": a fixing reference, not a component.
FIXING_BEFORE_RE = re.compile(r"\b(CLAMP\w*|SUPPORT\w*|FIXED|ROUTED|LAID|RUN|TIED|SECURED|ALIGNED)\W*$")
ACTIVITY_WORDS = re.compile(r"\b(SUPPLY(ING)?|INSTALLATION|TESTING|COMMISSIONING|PROVIDING|FURNISHING|OF|AND)\b|&")
WITHOUT_RE = re.compile(r"\bWITHOUT\b.*?(?=\s-\s|-\s*\d|$)")


@dataclass
class ScopeItem:
    """One selectable thing a line asks for, for one activity."""
    label: str
    query: str
    activity: str  # supply | installation
    selection_type: str = "Explicit"  # Explicit | Inferred
    head_tokens: list[str] = field(default_factory=list)  # the noun phrase being asked for
    line_tokens: set[str] = field(default_factory=set)  # every word the line and section use
    attrs: Attrs | None = None  # this item's own technical requirements
    is_head: bool = False  # the line's main component (model rules apply to it)


@dataclass
class Requirement:
    text: str
    specification: str
    context: list[str]
    component: str
    activities: list[str]
    attrs: Attrs
    additional: list[str]
    included: list[str]
    scope_items: list[ScopeItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "component": self.component, "specification": self.specification,
            "activities": self.activities, "additional_requirements": self.additional,
            "included_in_line": self.included, **self.attrs.as_dict(),
        }


def strip_activity(text: str) -> str:
    t = ACTIVITY_WORDS.sub(" ", text)
    return re.sub(r"\s+", " ", t).strip(" -,:.")


def split_clauses(text: str) -> tuple[str, list[str]]:
    """Head phrase, plus the 'including / with / +' clauses that may name further components."""
    marks = list(CLAUSE_RE.finditer(text))
    if not marks:
        return text, []
    extras = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        if FIXING_BEFORE_RE.search(text[:m.start()]):
            continue
        extras.append(text[m.end():end])
    return text[:marks[0].start()], extras


def precedent_key(text: str, context: list[str]) -> str:
    """Identity of a BOQ line for learning from engineers' earlier decisions."""
    return f"{canon(text)}|{canon(context[0]) if context else ''}"


def _activities(own: str, context: list[str], cost_sheet_type: str) -> list[str]:
    for text in [own, *context]:
        found = []
        if SUPPLY_RE.search(text):
            found.append("supply")
        if INSTALL_RE.search(text):
            found.append("installation")
        if found:
            return found
    t = REGISTRY.get(cost_sheet_type)
    return [t.activity] if t else ["supply"]


def head_noun_tokens(subject: str) -> list[str]:
    """Words that name the thing: 'CANVAS CONNECTION FOR DUCTABLE MACHINE' is a canvas connection."""
    t = WITHOUT_RE.sub(" ", subject)
    before_for = re.split(r"\bFOR\b", t, maxsplit=1)[0]
    return tokens(before_for) or tokens(t)


def _is_spec_only(own: str) -> bool:
    """True for lines like '40mm' or '11.0 Tr' that carry no component words themselves."""
    return not tokens(re.sub(r"\d+(\.\d+)?\s*[A-Z]{0,3}\b", " ", own))


OWN_OVERRIDES = ("sizes", "dims", "models", "gauge", "sqmm", "core", "refrigerants", "forms", "sides")


def build_requirement(line: SalesLine) -> Requirement:
    own_raw = line.row.text
    own = canon(own_raw)
    ctx = [canon(c) for c in line.context]
    activities = _activities(own, ctx, line.row.cost_sheet_type)

    included = [strip_activity(m.group(1)) for m in INCLUDED_RE.finditer(own)]
    cleaned_own = INCLUDED_RE.sub(" ", own)
    own_head, own_extras = split_clauses(cleaned_own)
    attrs_own = extract_attrs(own_head)

    # The nearest section supplies the subject when the line is only a specification ("40mm")
    # or a model under an equipment heading ("2.01 TR FXKQ63ARV16").
    spec_only = _is_spec_only(strip_activity(own_head))
    parent = ctx[0] if ctx else ""
    if parent and (spec_only or attrs_own.models):
        parent_head, parent_extras = split_clauses(parent)
        head = f"{strip_activity(parent_head)} {strip_activity(own_head)}".strip()
        extras = parent_extras + own_extras
    else:
        head = strip_activity(own_head)
        extras = own_extras
    extras = [strip_activity(e) for e in extras]
    extras = [e for e in extras if tokens(e) and not PROCESS_RE.search(e)]
    additional = list(dict.fromkeys(extras))

    # Head attributes come from head phrases only: the line, then its section headings.
    section_heads = [split_clauses(c)[0] for c in ctx[:2]]
    attrs = extract_attrs(" ".join([own_head, *section_heads]))
    for key in OWN_OVERRIDES:  # a line's own figures override anything in its ancestors
        own_value = getattr(attrs_own, key)
        if own_value:
            setattr(attrs, key, set(own_value))
    attrs.capacity.update(attrs_own.capacity)

    req = Requirement(text=own_raw, specification=own_raw if spec_only else "", context=line.context,
                      component=head.title(), activities=activities, attrs=attrs,
                      additional=additional, included=included)
    line_tokens = set(tokens(WITHOUT_RE.sub(" ", head))) | set(tokens(WITHOUT_RE.sub(" ", own_head)))
    req.scope_items = _scope_items(req, head, additional)
    for item in req.scope_items:
        item.line_tokens = line_tokens | set(item.head_tokens)
    return req


def _scope_items(req: Requirement, head: str, additional: list[str]) -> list[ScopeItem]:
    spec = strip_activity(canon(req.specification)) if req.specification else ""
    items: list[ScopeItem] = []
    subjects = [(head, "Explicit", req.attrs, True)]
    for extra in additional:
        text = f"{extra} {spec}".strip() if spec and spec not in extra else extra
        subjects.append((text, "Explicit", extract_attrs(text), False))
    for subject, kind, attrs, is_head in subjects:
        for activity in req.activities:
            query = f"INSTALLATION OF {subject}" if activity == "installation" else subject
            items.append(ScopeItem(label=subject.title(), query=query, activity=activity, selection_type=kind,
                                   head_tokens=head_noun_tokens(subject), attrs=attrs, is_head=is_head))
    for inc in req.included:
        # "Remote Controller Included" is part of the unit price; real BOQs do not add a child.
        items.append(ScopeItem(label=inc.title(), query=inc, activity="supply", selection_type="Inferred",
                               head_tokens=tokens(inc), attrs=extract_attrs(inc)))
    return items
