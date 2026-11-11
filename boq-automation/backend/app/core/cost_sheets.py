"""Cost Sheet Type registry.

Adding a type to scope is a configuration change: list it in ENABLED_COST_SHEET_TYPES.
A type absent from this registry is treated as out of scope everywhere.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostSheetType:
    name: str  # exact spelling used by Sales BOM column H and the Item Master
    group: str
    activity: str  # supply | installation


REGISTRY: dict[str, CostSheetType] = {
    t.name: t
    for t in (
        CostSheetType("Highside Supply", "Highside", "supply"),
        CostSheetType("Highside Installation", "Highside", "installation"),
        CostSheetType("Lowside Supply", "Lowside", "supply"),
        CostSheetType("Lowside Installation", "Lowside", "installation"),
    )
}


def type_for(group: str, activity: str) -> str | None:
    for t in REGISTRY.values():
        if t.group == group and t.activity == activity:
            return t.name
    return None
