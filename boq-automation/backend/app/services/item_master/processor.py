"""Read the Item Master and build searchable component records."""
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.services.excel_processor.workbook import ITEM_MASTER_REQUIRED, WorkbookError, open_values

from .normalize import Attrs, canon, extract_attrs, is_deprecated, looks_like_installation, tokens


@dataclass
class Component:
    item_no: str
    item_brief: str
    cost_sheet_type: str  # "" when blank in the master; such items are never auto-selected
    uom: str
    cost: float
    family: str = ""
    description: str = ""
    raw: dict = field(default_factory=dict)  # the original record: the source of truth
    attrs: Attrs = field(default_factory=Attrs)
    canon_brief: str = ""
    token_set: set[str] = field(default_factory=set)
    deprecated: bool = False
    install_like: bool = False

    @property
    def class_name(self) -> str:
        """All 33 mapped rows in the source BOQ follow CO- = Component, IN- = Installation."""
        return "Installation" if self.item_no.upper().startswith("IN-") else "Component"

    @property
    def search_text(self) -> str:
        parts = [self.canon_brief]
        d = canon(self.description)
        if d and d != self.canon_brief and not d.replace(".", "").isdigit():
            parts.append(d)
        return " ".join(parts)


def _clean(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def build_component(raw: dict) -> Component:
    try:
        cost = float(raw.get("Cost") or 0)
    except ValueError:
        cost = 0.0
    brief = _clean(raw.get("Item Brief"))
    c = Component(item_no=_clean(raw.get("Item No")), item_brief=brief,
                  cost_sheet_type=_clean(raw.get("Cost Sheet Type")),
                  uom=_clean(raw.get("QuantityUom")), cost=cost, family=_clean(raw.get("Family")),
                  description=_clean(raw.get("Description")), raw=raw)
    enrich(c)
    return c


def enrich(c: Component) -> None:
    c.canon_brief = canon(c.item_brief)
    c.attrs = extract_attrs(c.item_brief)
    # A short brief may omit the model or form that the Description or Model column states.
    extra = " ".join(v for v in (c.description, c.raw.get("Model", "")) if v)
    if extra:
        more = extract_attrs(extra)
        c.attrs.models |= more.models
        c.attrs.forms |= more.forms
        c.attrs.sides |= more.sides
        c.attrs.brands |= more.brands
    c.token_set = set(tokens(c.canon_brief))
    c.deprecated = is_deprecated(c.item_brief)
    c.install_like = looks_like_installation(c.item_brief)


def read_item_master(path: str | Path, sheet: str | None = None) -> list[Component]:
    wb = open_values(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(rows)]
    missing = [c for c in ITEM_MASTER_REQUIRED if c not in header]
    if missing:
        wb.close()
        raise WorkbookError("Item Master is missing columns: " + ", ".join(missing))
    out = []
    for values in rows:
        raw = {h: _clean(v) for h, v in zip(header, values) if h}
        if raw.get("Item No"):
            out.append(build_component(raw))
    wb.close()
    return out


def data_quality(components: list[Component]) -> dict:
    briefs: dict[str, list[Component]] = {}
    for c in components:
        briefs.setdefault(c.canon_brief, []).append(c)
    dup = [v for v in briefs.values() if len(v) > 1]
    conflicting = [v for v in dup if len({x.cost_sheet_type for x in v}) > 1]
    counts: dict[str, int] = {}
    for c in components:
        counts[c.cost_sheet_type or "(blank)"] = counts.get(c.cost_sheet_type or "(blank)", 0) + 1
    return {
        "total": len(components), "by_cost_sheet_type": counts,
        "blank_cost_sheet_type": counts.get("(blank)", 0),
        "duplicate_brief_groups": len(dup), "conflicting_cost_sheet_groups": len(conflicting),
        "deprecated": sum(c.deprecated for c in components),
        "zero_cost": sum(c.cost == 0 for c in components),
        "supply_items_worded_as_installation": sum(
            c.install_like and c.cost_sheet_type.endswith("Supply") for c in components),
    }
