"""Validation engine: the Item Master decides which records exist; verified knowledge decides
how they are classified; whoever proposed the component."""
from app.core.cost_sheets import REGISTRY
from app.services.boq_parser.parser import SalesLine
from app.services.item_master.processor import Component, reclassified


def component_flags(c: Component, selected_types: list[str] | None = None) -> list[str]:
    """Problems with the master record itself. Any flag forces manual review."""
    flags = []
    if not c.cost_sheet_type:
        flags.append("Cost Sheet Type is blank in the Item Master")
    elif c.cost_sheet_type not in REGISTRY:
        flags.append(f"Cost Sheet Type '{c.cost_sheet_type}' is not supported")
    elif selected_types and c.cost_sheet_type not in selected_types:
        flags.append(f"Cost Sheet Type '{c.cost_sheet_type}' is outside the selected scope")
    t = REGISTRY.get(c.cost_sheet_type)
    if t and t.activity == "installation" and not c.install_like:
        flags.append("Listed under installation but worded as equipment; check the Item Master")
    if t and t.activity == "supply" and c.install_like:
        flags.append("Listed under supply but worded as installation; check the Item Master")
    if c.deprecated:
        flags.append("Marked NA/N-A in the Item Master (retired)")
    return flags


def classify(c: Component, knowledge=None, evidence=None):
    """The record with its recommended Cost Sheet Type, and how that type was decided.

    Priority: verified historical BOQs > approved review corrections > Item Master. The Item
    Master's own value always stays available as original_cost_sheet_type."""
    from app.services.knowledge.index import Classification

    master_type = c.original_cost_sheet_type
    if knowledge is None:
        return c, Classification(c.cost_sheet_type, "item_master" if c.cost_sheet_type == master_type else "historical")
    local = evidence.items.get(c.item_no) if evidence is not None else None
    cls = knowledge.classifier.resolve(c.item_no, master_type, local)
    if cls.cost_sheet_type == c.cost_sheet_type:
        return c, cls
    base = c if c.master_cost_sheet_type is None else reclassified(c, master_type)
    return (base if cls.cost_sheet_type == master_type else reclassified(base, cls.cost_sheet_type)), cls


def finalize_line(result, indexes, line: SalesLine, selected_types: list[str], knowledge=None,
                  evidence=None) -> None:
    """Re-check every selection against the master; drop anything invalid or duplicated."""
    existing = {ch.item_no for ch in line.children if ch.item_no}
    seen: set[str] = set()
    kept = []
    for s in result.selections:
        master = indexes.by_no.get(s.component.item_no)
        if master is None:
            result.notes.append(f"{s.component.item_no} is not in the Item Master; discarded.")
            continue
        if master.item_no in seen:
            continue
        if master.item_no in existing:
            result.notes.append(f"{master.item_no} is already mapped under this line; not added again.")
            continue
        master, cls = classify(master, knowledge, evidence)
        if not master.cost_sheet_type or master.cost_sheet_type not in REGISTRY \
                or master.cost_sheet_type not in selected_types:
            why = f" ({cls.note})" if cls.note else ""
            result.unmatched.append(f"{s.scope_label}: {master.item_no} has Cost Sheet Type "
                                    f"'{master.cost_sheet_type or 'blank'}'{why}; manual review needed")
            continue
        seen.add(master.item_no)
        s.component = master
        s.classification = cls
        ev = evidence.items.get(master.item_no) if evidence is not None else None
        s.evidence = list(ev.references) if ev else []
        s.flags = list(dict.fromkeys(s.flags + component_flags(master, selected_types)))
        if cls.conflict:
            s.flags.append(cls.note)
        if master.cost <= 0:
            s.flags.append("Item Master cost is 0; Column N stays 0 until an estimator prices it")
        if line.row.uom and master.uom and master.uom.upper() != line.row.uom.upper():
            s.flags.append(f"Item Master cost is per {master.uom}; this BOQ line is in {line.row.uom}")
        kept.append(s)
    result.selections = kept


def validate_for_export(item_no: str, cost_sheet_type: str, indexes_by_no: dict, enabled_types: list[str]) -> list[str]:
    """Blocking errors, checked again immediately before a row is written.

    The Item Number must exist in the Item Master. The Cost Sheet Type is the reviewed
    recommendation, which may differ from the master's value by design, but it must be a
    supported type within this run's scope."""
    if indexes_by_no.get(item_no) is None:
        return [f"{item_no} is not in the Item Master"]
    if not cost_sheet_type:
        return [f"{item_no} has no Cost Sheet Type"]
    if cost_sheet_type not in REGISTRY or cost_sheet_type not in enabled_types:
        return [f"{item_no}: Cost Sheet Type '{cost_sheet_type}' is outside this run's scope"]
    return []
