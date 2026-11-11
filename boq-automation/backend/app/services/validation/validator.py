"""Validation engine: the Item Master is the source of truth, whoever proposed the component."""
from app.core.cost_sheets import REGISTRY
from app.services.boq_parser.parser import SalesLine
from app.services.item_master.processor import Component


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


def finalize_line(result, indexes, line: SalesLine, selected_types: list[str]) -> None:
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
        if not master.cost_sheet_type or master.cost_sheet_type not in REGISTRY \
                or master.cost_sheet_type not in selected_types:
            result.unmatched.append(f"{s.scope_label}: {master.item_no} has Cost Sheet Type "
                                    f"'{master.cost_sheet_type or 'blank'}'; manual review needed")
            continue
        seen.add(master.item_no)
        s.component = master
        s.flags = list(dict.fromkeys(s.flags + component_flags(master, selected_types)))
        if master.cost <= 0:
            s.flags.append("Item Master cost is 0; Column N stays 0 until an estimator prices it")
        if line.row.uom and master.uom and master.uom.upper() != line.row.uom.upper():
            s.flags.append(f"Item Master cost is per {master.uom}; this BOQ line is in {line.row.uom}")
        kept.append(s)
    result.selections = kept


def validate_for_export(item_no: str, recorded_cost_sheet_type: str, indexes_by_no: dict) -> list[str]:
    """Blocking errors, checked again immediately before a row is written."""
    errors = []
    master = indexes_by_no.get(item_no)
    if master is None:
        return [f"{item_no} is not in the Item Master"]
    if not master.cost_sheet_type:
        errors.append(f"{item_no} has a blank Cost Sheet Type in the Item Master")
    elif master.cost_sheet_type != recorded_cost_sheet_type:
        errors.append(f"{item_no}: recorded Cost Sheet Type '{recorded_cost_sheet_type}' does not match "
                      f"the Item Master value '{master.cost_sheet_type}'")
    return errors
