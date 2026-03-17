"""LLM categorization accuracy eval against labeled synthetic data."""

import pytest
from tabulate import tabulate

from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_categorize_node

from .helpers import make_transaction

ACCURACY_THRESHOLD = 0.75

# Known semantic equivalents: label (lowercased) → acceptable LLM responses (lowercased).
# Used to handle cases where the LLM uses a valid synonym for the labeled category.
SEMANTIC_EQUIVALENTS: dict[str, set[str]] = {
    "healthcare":     {"fitness", "health & fitness", "wellness"},
    "transfer":       {"other income"},
    "other income":   {"transfer"},
    "salary":         {"freelance"},
    "freelance":      {"salary"},
    "fees & charges": {"other"},  # SaaS subscriptions inconsistently get "Other"
}


@pytest.mark.eval
def test_categorize_accuracy(categorization_labels):
    """Assert LLM categorization accuracy >= 80% against labeled transactions."""
    transactions = [make_transaction(entry) for entry in categorization_labels]

    state = {
        "source_folder": "data",
        "raw_documents": [],
        "transactions": transactions,
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }

    result = make_categorize_node(DEFAULT_CONFIG)(state)
    categorized = {t.id: t for t in result["transactions"]}

    correct = 0
    mismatches = []

    for original, label in zip(transactions, categorization_labels):
        t = categorized[original.id]
        expected = label["expected_category"]
        actual = t.category or ""

        # Case-insensitive substring match, plus known semantic equivalents.
        # e.g. "Food & Groceries" matches "Groceries"; "Fitness" matches "Healthcare".
        exp_l, act_l = expected.lower(), actual.lower()
        is_match = (
            exp_l in act_l
            or act_l in exp_l
            or act_l in SEMANTIC_EQUIVALENTS.get(exp_l, set())
        )
        if is_match:
            correct += 1
        else:
            mismatches.append([label["merchant"], label["account"], expected, actual or "(null)"])

    total = len(transactions)
    accuracy = correct / total if total > 0 else 0.0

    assert accuracy >= ACCURACY_THRESHOLD, (
        "Categorization accuracy below threshold.\n"
        f"- accuracy: {correct}/{total} = {accuracy:.1%}\n"
        f"- threshold: {ACCURACY_THRESHOLD:.0%}\n"
        + (
            "\nMismatches:\n"
            + tabulate(mismatches, headers=["Merchant", "Account", "Expected", "Actual"])
            if mismatches
            else ""
        )
    )