"""Which BOQ lines may be mapped to Item Master components.

Mandatory rule: a line is mapped only when it has BOTH a valid unit and a valid quantity,
checked on that line's own cells. Nothing is assumed or filled in from other rows.

  unit      blank, "-", "NA", "nil" or a bare number    -> missing
  quantity  blank or "-"                                -> missing
            text such as "LS", "Lot", "as reqd"         -> invalid (not a number)
            negative                                    -> invalid
            0                                           -> zero: by the run's rule, either
                                                           mapped and flagged for review, or skipped
            positive number                             -> valid
"""
from dataclasses import dataclass

SKIP_LABEL = "Skipped – Unit/Quantity Missing"
ZERO_FLAG = "BOQ quantity is 0; confirm this item is required before approving"
ZERO_RULES = ("review", "skip")
_BLANK = {"", "-", "--", "—", "–", "na", "n/a", "nil", "none", "null", "."}


def unit_valid(unit: str) -> bool:
    t = (unit or "").strip()
    if t.lower() in _BLANK:
        return False
    try:
        float(t.replace(",", ""))
        return False  # a number in the unit column is not a unit
    except ValueError:
        return True


def quantity_state(raw) -> tuple[str, float]:
    """(state, value) with state one of valid | zero | missing | invalid."""
    if raw is None or isinstance(raw, bool):
        return "missing", 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip()
        if text.lower() in _BLANK:
            return "missing", 0.0
        try:
            value = float(text.replace(",", ""))
        except ValueError:
            return "invalid", 0.0
    if value < 0:
        return "invalid", value
    return ("zero" if value == 0 else "valid"), value


@dataclass
class Eligibility:
    eligible: bool
    reason: str = ""  # why it was skipped, shown to the engineer
    flag: str = ""  # added to every suggestion when the line is mapped with a caveat


def check(unit: str, qty_state: str, qty_raw: str, zero_rule: str = "review") -> Eligibility:
    problems = []
    if not unit_valid(unit):
        problems.append("unit is blank" if not (unit or "").strip() else f"unit \"{unit.strip()}\" is not a unit")
    if qty_state == "missing":
        problems.append("quantity is blank")
    elif qty_state == "invalid":
        problems.append(f"quantity \"{qty_raw}\" is not a valid number")
    elif qty_state == "zero" and zero_rule == "skip":
        problems.append("quantity is 0")
    if problems:
        return Eligibility(False, f"{SKIP_LABEL}: {'; '.join(problems)}")
    if qty_state == "zero":
        return Eligibility(True, flag=ZERO_FLAG)
    return Eligibility(True)
