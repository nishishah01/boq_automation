import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT.parent / "sample_data"

_tmp = tempfile.mkdtemp(prefix="boqtest")
# Never read the real backend/.env in tests: it can hold live keys, and it outranks these variables.
os.environ["BOQ_ENV_FILE"] = f"{_tmp}/no.env"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["AI_MODE"] = "mock"
os.environ["ANTHROPIC_API_KEY"] = ""


@pytest.fixture(scope="session")
def sample_boq() -> Path:
    return SAMPLE / "sample_boq.xlsm"


@pytest.fixture(scope="session")
def sample_master() -> Path:
    return SAMPLE / "sample_item_master.xlsx"


@pytest.fixture(scope="session")
def components(sample_master):
    from app.services.item_master.processor import read_item_master
    return read_item_master(sample_master)


@pytest.fixture(scope="session")
def indexes(components):
    from app.services.ai_matching.retrieval import MasterIndexes
    return MasterIndexes(components)


@pytest.fixture(scope="session")
def parsed(sample_boq):
    from app.services.boq_parser.parser import parse_boq
    return parse_boq(sample_boq)


@pytest.fixture(scope="session")
def engine(indexes):
    from app.services.ai_matching.engine import MatchConfig, MatchingEngine
    return MatchingEngine(indexes, MatchConfig(["Highside Supply", "Highside Installation"]))


@pytest.fixture
def line(parsed):
    return lambda uno: next(l for l in parsed.lines if l.row.unique_no == uno)
