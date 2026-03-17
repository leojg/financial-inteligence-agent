"""Fixtures for the eval test suite."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env with override=True so the real key wins over the "test-key-not-used"
# placeholder that tests/conftest.py sets via os.environ.setdefault().
load_dotenv(override=True)

LABELS_PATH = Path(__file__).parent.parent.parent / "data" / "eval_labels.json"


@pytest.fixture(autouse=True)
def require_real_api_key():
    """Skip eval tests when no real OPENAI_API_KEY is present."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or key == "test-key-not-used":
        pytest.skip("Requires a real OPENAI_API_KEY — set it to run eval tests")


@pytest.fixture(scope="session")
def eval_labels():
    """Load eval_labels.json, skipping if missing."""
    if not LABELS_PATH.exists():
        pytest.skip(
            f"eval_labels.json not found at {LABELS_PATH}. "
            "Run `python scripts/generate_samples.py` first."
        )
    return json.loads(LABELS_PATH.read_text())


@pytest.fixture(scope="session")
def categorization_labels(eval_labels):
    return eval_labels["categorization"]


@pytest.fixture(scope="session")
def duplicate_pairs_labels(eval_labels):
    return eval_labels["duplicate_pairs"]


@pytest.fixture(scope="session")
def non_duplicate_pairs_labels(eval_labels):
    return eval_labels["non_duplicate_pairs"]
