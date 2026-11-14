"""HVAC/MEP nomenclature normalisation shared by BOQ text and Item Master records.

Item Master columns such as Size, Make and Model are unreliable: they were populated by
splitting the Item Brief (Model holds values like "wall", "UNIT", "CHARGES"). Technical
attributes are therefore parsed from the text itself.
"""
import re
from dataclasses import dataclass, field

# Applied in order on upper-cased text.
PHRASES: list[tuple[str, str]] = [
    (r"\bS\.?I\.?T\.?C\.?\b", "SUPPLY INSTALLATION TESTING COMMISSIONING"),
    (r"\bINSTALLATIONOF\b", "INSTALLATION OF"),
    (r"\bINSTALL?AS?T?ION\b|\bINSTALLASTION\b|\bINSTALL?TION\b", "INSTALLATION"),
    (r"\bTABLE TOP STRUCTURE\b", "TABLE TOP STAND"),
    (r"\bDISMANTL\w*\b", "DISMANTLING"),
    (r"\bDGT\b|\bDOUBLE GAS TIGHT\b", "GAS TIGHT"),
    (r"\bALPHA\b", "ALFA"),
    (r"\bCABLING\b", "CABLE"),
    (r"\bR[- ]?410[- ]?A?\b", "R410A"),
    (r"\bR[- ]?32\b", "R32"),
    (r"\bR[- ]?22\b", "R22"),
    (r"\bR[- ]?407[- ]?C\b", "R407C"),
    (r"\bINSTALL?ATON\b|\bINSTALATION\b|\bINSTALLTION\b", "INSTALLATION"),
    (r"\bINSTALLING\b|\bINSTALLED\b", "INSTALLATION"),
    (r"\bFIXING\b|\bERECTION\b|\bMOUNTING\b", "INSTALLATION"),
    (r"\bPIPING\b|\bPIPES\b|\bPIPEWORK\b", "PIPE"),
    (r"\bDUCTING\b|\bDUCTWORK\b", "DUCT"),
    (r"\bOUTDOOR UNITS?\b|\bODUS?\b|\bCONDENSING UNITS?\b", "ODU"),
    (r"\bINDOOR UNITS?\b|\bINDOORS?\b|\bIDUS?\b", "IDU"),
    (r"\bAIR HANDL?I?N?G UNITS?\b|\bAHUS\b", "AHU"),
    (r"\bFAN COIL UNITS?\b|\bFCUS\b", "FCU"),
    (r"\bHI[- ]?WALL\b|\bHIGH[- ]WALL\b", "HIWALL"),
    (r"\bDUCTED\b|\bDUCTABLE\b", "DUCTABLE"),
    (r"\bVRF\b|\bNVRV\b", "VRV"),
    (r"\bREMORTE?\b", "REMOTE"),
    (r"\bCONTROLLAR\b|\bCONTROLLERS\b", "CONTROLLER"),
    (r"\bCANVASS\b", "CANVAS"),
    (r"\bALUMINUM\b", "ALUMINIUM"),
    (r"\bMILD STEEL\b", "MS"),
    (r"\bGALVAN[IS]ZED( IRON)?\b|\bGALVANI[SZ]ED( IRON)?\b", "GI"),
    (r"\bSTAINLESS STEEL\b", "SS"),
    (r"\bCU\b", "COPPER"),
    (r"\bANTI[- ]?CORROSI(ON|VE)\b", "ANTICORROSION"),
    (r"\bMOTORI[SZ]ED\b", "MOTORISED"),
    (r"\bPANELS\b", "PANEL"),
    (r"\bFANS\b", "FAN"),
    (r"\bCABLES\b", "CABLE"),
    (r"\bVALVES\b", "VALVE"),
    (r"\bPUMPS\b", "PUMP"),
    (r"\bUNITS\b", "UNIT"),
    (r"\bGRILLS?\b|\bGRILLES\b", "GRILLE"),
    (r"\bINSULATED\b", "INSULATION"),
]

STOP = {
    "A", "AN", "AND", "THE", "OF", "FOR", "WITH", "IN", "ON", "TO", "AS", "AT", "BY", "ETC", "ALL", "OR",
    "SUPPLY", "SUPPLYING", "INSTALLATION", "TESTING", "COMMISSIONING", "PROVIDING", "FURNISHING",
    "MAKE", "TYPE", "INCLUDED", "INCLUDING", "COMPLETE", "ITEM", "ITEMS", "LOT", "SET", "NOS", "MM", "INCH",
    "DIA", "SIZE", "TR", "HP", "CFM", "KW", "WORK", "WORKS", "PROPERLY", "ADEQUATE", "BETWEEN", "NON",
    "FOLLOWING", "CAPACITIES", "CAPACITY", "SPECIFICATION", "SPECIFICATIONS", "PER", "NO", "NOS.",
}

# Equipment forms that are not interchangeable. A generic CASSETTE is compatible with any
# cassette subtype; two different specific forms are a hard conflict.
CASSETTE_FORMS = [
    ("CASSETTE_1WAY", r"\b(?:1|ONE)[- ]?WAY\b"),
    ("CASSETTE_2WAY", r"\b(?:2|TWO)[- ]?WAY\b"),
    ("CASSETTE_4WAY", r"\b(?:4|FOUR)[- ]?WAY\b|\bROUND[- ]?FLOW\b"),
    ("CASSETTE_CORNER", r"\bCORNER\b"),
]
OTHER_FORMS = [
    ("HIWALL", r"\bHIWALL\b|\bWALL MOUNTED\b"),
    ("DUCTABLE", r"\bDUCTABLE\b"),
    ("TOWER", r"\bTOWER\b|\bFLOOR STANDING\b"),
    ("CEILING_SUSPENDED", r"\bCEILING SUSPENDED\b|\bUNDER CEILING\b"),
]


def equipment_forms(t: str) -> set[str]:
    """t must be canonical text."""
    if re.search(r"\bCASSETTE\b", t):
        found = {name for name, rx in CASSETTE_FORMS if re.search(rx, t)}
        return found or {"CASSETTE"}
    return {name for name, rx in OTHER_FORMS if re.search(rx, t)}


def forms_conflict(required: set[str], offered: set[str]) -> bool:
    if not required or not offered:
        return False
    def widen(s: set[str]) -> set[str]:
        return s | ({"CASSETTE"} if any(x.startswith("CASSETTE") for x in s) else set())
    req, off = widen(required), widen(offered)
    specific_req = {x for x in req if x != "CASSETTE"}
    specific_off = {x for x in off if x != "CASSETTE"}
    if specific_req and specific_off:
        return not (specific_req & specific_off)
    return not (req & off)


def unit_sides(t: str) -> set[str]:
    return {s for s in ("IDU", "ODU") if re.search(rf"\b{s}\b", t)}
BRANDS = ["DAIKIN", "MITSUBISHI", "BLUESTAR", "MIDEA", "CARRIER", "VOLTAS", "TRANE", "SAMSUNG", "HITACHI",
          "TOSHIBA", "CARYAIRE", "LG", "PANASONIC", "HISENSE", "O GENERAL"]
MATERIALS = ["UPVC", "CPVC", "PVC", "HDPE", "PPR", "COPPER", "GI", "MS", "SS", "ALUMINIUM", "BRASS",
             "NITRILE", "XLPE", "FIBREGLASS"]
# Materials a BOQ may use loosely for one another: a soft penalty, not a rejection.
MATERIAL_FAMILIES = [{"PVC", "UPVC"}]
NOT_MODELS = re.compile(r"^(R\d{2,3}[A-Z]?|IP\d+|\d+[A-Z]{1,5}|DN\d+|PN\d+|SCH\d+|M\d+|\d+X\d+)$")

SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:MM\b|MM\.|M\.M\b)")
DN_RE = re.compile(r"\b(?:DN|NB)\s*-?\s*(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s*NB\b")
PHI_RE = re.compile(r"[Ø]\s*(\d+(?:\.\d+)?)")
INCH_RE = re.compile(r"(?<![\d./])(\d+(?:\.\d+)?|\d+/\d+)\s*(?:\"|INCH\b|INCHES\b)")
DIM_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:MM)?\s*[X*]\s*(\d+(?:\.\d+)?)(?:\s*(?:MM)?\s*[X*]\s*(\d+(?:\.\d+)?))?")
TRAILING_NUM_RE = re.compile(r"(?:\s|-)(\d{2,4})\s*$")
UNIT_RES = {
    "tr": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:TR|TONS?|TONNAGE)\b"),
    "hp": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*HP\b"),
    "cfm": re.compile(r"(?<![\d.])(\d+(?:,\d{3})*(?:\.\d+)?)\s*CFM\b"),
    "kw": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*KW\b"),
}
SQMM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*SQ\.?\s*MM\b")
CORE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*CORE\b")
GAUGE_RE = re.compile(r"(?<![\d.])(\d{2})\s*(?:G\b|GA\b|GAUGE\b|GUAGE\b|SWG\b)")
MODEL_RE = re.compile(r"(?<![A-Z0-9])[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*(?![A-Z0-9])")
DEPRECATED_RE = re.compile(r"^\s*(N\s*/?\s*A)\b", re.I)
INSTALL_RE = re.compile(r"\b(INSTALL\w*|INSTALATION|INSTALATON|ERECTION|FIXING)\b", re.I)


def canon(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").upper().replace(" ", " ")).strip()
    for pat, rep in PHRASES:
        t = re.sub(pat, rep, t)
    return t


def stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("S") and not word.endswith("SS") else word


def search_terms(text: str) -> list[str]:
    """Words and word pairs for text search, singular, so SLEEVES finds SLEEVE and the reverse."""
    words = [stem(w) for w in re.findall(r"[A-Z0-9][A-Z0-9.]*", text)]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def tokens(text: str) -> list[str]:
    """Meaningful words of already-canonical text."""
    out = []
    for w in re.findall(r"[A-Z][A-Z0-9]+|[A-Z]", text):
        if w in STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("S") and not w.endswith("SS") else w)
    return out


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _inch(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        return round(float(a) / float(b) * 25.4, 1)
    return round(float(s) * 25.4, 1)


@dataclass
class Attrs:
    sizes: set[float] = field(default_factory=set)
    soft_sizes: set[float] = field(default_factory=set)  # trailing bare numbers: "JET NOZZLE 400"
    dims: set[tuple] = field(default_factory=set)
    capacity: dict[str, float] = field(default_factory=dict)
    sqmm: set[float] = field(default_factory=set)
    core: set[float] = field(default_factory=set)
    gauge: set[int] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    brands: set[str] = field(default_factory=set)
    materials: set[str] = field(default_factory=set)
    forms: set[str] = field(default_factory=set)
    sides: set[str] = field(default_factory=set)
    refrigerants: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "sizes_mm": sorted(self.sizes | self.soft_sizes), "dimensions": sorted(self.dims),
            "capacity": self.capacity, "sqmm": sorted(self.sqmm), "core": sorted(self.core),
            "gauge": sorted(self.gauge), "models": sorted(self.models), "brands": sorted(self.brands),
            "materials": sorted(self.materials), "equipment_form": sorted(self.forms),
            "unit_side": sorted(self.sides), "refrigerant": sorted(self.refrigerants),
        }

    _SETS = ("sizes", "soft_sizes", "sqmm", "core", "gauge", "models", "brands", "materials", "forms", "sides",
             "refrigerants")

    def dump(self) -> dict:
        """Lossless JSON form, stored with a knowledge record so it need not be re-derived."""
        out = {k: sorted(getattr(self, k)) for k in self._SETS}
        out["dims"] = sorted(list(d) for d in self.dims)
        out["capacity"] = dict(self.capacity)
        return out

    @classmethod
    def load(cls, d: dict) -> "Attrs":
        a = cls(**{k: set(d.get(k) or []) for k in cls._SETS})
        a.dims = {tuple(x) for x in d.get("dims") or []}
        a.capacity = dict(d.get("capacity") or {})
        return a


def norm_model(m: str) -> str:
    return re.sub(r"[-/\s]", "", m)


def extract_models(text: str) -> set[str]:
    out = set()
    for m in MODEL_RE.findall(text):
        n = norm_model(m)
        if len(n) >= 5 and re.search(r"\d", n) and re.search(r"[A-Z]", n) and not NOT_MODELS.match(n):
            out.add(n)
    return out


def joined_models(text: str) -> dict[str, set[str]]:
    """Models written with spaces or separators: 'FXAQ 32 ARV16' -> {'FXAQ32ARV16': {'ARV16', ...}}.

    These are only candidates; the caller keeps one only when it exists in the Item Master,
    so ordinary neighbouring words are never mistaken for a model.
    """
    parts = re.findall(r"[A-Z0-9]+", canon(text))
    out: dict[str, set[str]] = {}
    for n in (2, 3):
        for i in range(len(parts) - n + 1):
            group = parts[i:i + n]
            joined = "".join(group)
            if len(joined) >= 6 and re.search(r"[A-Z]", joined) and re.search(r"\d", joined) \
                    and joined[0].isalpha() and not NOT_MODELS.match(joined):
                out.setdefault(joined, set()).update(g for g in group)
    return out


def extract_attrs(text: str) -> Attrs:
    t = canon(text)
    a = Attrs()
    dims_spans = []
    for m in DIM_RE.finditer(t):
        vals = tuple(sorted(float(x) for x in m.groups() if x))
        a.dims.add(vals)
        dims_spans.append(m.span())
    masked = t
    for s, e in reversed(dims_spans):
        masked = masked[:s] + " " * (e - s) + masked[e:]
    masked_nosq = SQMM_RE.sub(" ", masked)
    a.sizes.update(float(x) for x in SIZE_RE.findall(masked_nosq))
    a.sizes.update(float(x or y) for x, y in DN_RE.findall(masked))
    a.sizes.update(float(x) for x in PHI_RE.findall(t))
    a.sizes.update(_inch(x) for x in INCH_RE.findall(masked))
    for key, rx in UNIT_RES.items():
        m = rx.search(t)
        if m:
            a.capacity[key] = _num(m.group(1))
    a.sqmm.update(float(x) for x in SQMM_RE.findall(t))
    a.core.update(float(x) for x in CORE_RE.findall(t))
    a.gauge.update(int(x) for x in GAUGE_RE.findall(t))
    a.models = extract_models(t)
    # Daikin nomenclature: RXQ38BRY16 is a 38 HP outdoor unit.
    rxq = re.search(r"\bRXQ(\d{1,3})[A-Z]", t)
    if rxq and "hp" not in a.capacity:
        a.capacity["hp"] = float(rxq.group(1))
    a.brands = {b for b in BRANDS if re.search(rf"\b{b}\b", t)}
    if re.search(r"\bBLUE STAR\b", t):
        a.brands.add("BLUESTAR")
    a.materials = {m for m in MATERIALS if re.search(rf"\b{m}\b", t)}
    a.forms = equipment_forms(t)
    a.sides = unit_sides(t)
    a.refrigerants = set(re.findall(r"\b(R410A|R32|R22|R407C)\b", t))
    if a.sqmm and not a.core:  # "2 C + 1 SQ.MM" is a two-core cable
        a.core.update(float(x) for x in re.findall(r"(?<![\d.])(\d+)\s*C\b", t))
    if not a.sizes and not a.capacity and not a.dims and not a.gauge:
        m = TRAILING_NUM_RE.search(t)
        if m:
            a.soft_sizes.add(float(m.group(1)))
    return a


def is_deprecated(brief: str) -> bool:
    """The master marks retired items with an NA / N/A prefix."""
    return bool(DEPRECATED_RE.match(brief or ""))


def looks_like_installation(brief: str) -> bool:
    return bool(INSTALL_RE.search(brief or ""))
