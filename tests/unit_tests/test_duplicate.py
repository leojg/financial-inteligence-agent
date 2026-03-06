import pytest

from agent.configuration import DEFAULT_CONFIG
from agent.nodes import make_detect_duplicates_node
from agent.state import Transaction


def _base_state(transactions):
    """Common state shape for duplicate-detection tests."""
    return {
        "source_folder": "data",
        "raw_documents": [],
        "transactions": transactions,
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }


@pytest.fixture
def config():
    return DEFAULT_CONFIG


@pytest.fixture
def state_with_duplicates():
    """State with 4 transactions: tx-001 and tx-002 are exact duplicates (same date/amount)."""
    transactions = [
        Transaction(
            id="tx-001",
            date="2026-01-02",
            amount_original=-50.0,
            amount_base=-50.0,
            currency="USD",
            merchant="SUPERMARKET",
            account="Checking",
            source_file="data/account1.xlsx",
        ),
        Transaction(
            id="tx-002",
            date="2026-01-02",
            amount_original=-50.0,
            amount_base=-50.0,
            currency="USD",
            merchant="SUPERMARKET",
            account="Checking",
            source_file="data/account2.xlsx",
        ),  # Duplicate of tx-001
        Transaction(
            id="tx-003",
            date="2026-01-03",
            amount_original=-25.0,
            amount_base=-25.0,
            currency="USD",
            merchant="COFFEE SHOP",
            account="Checking",
            source_file="data/account1.xlsx",
        ),
        Transaction(
            id="tx-004",
            date="2026-01-04",
            amount_original=-100.0,
            amount_base=-100.0,
            currency="USD",
            merchant="RESTAURANT",
            account="Checking",
            source_file="data/account1.xlsx",
        ),
    ]
    return _base_state(transactions)


@pytest.fixture
def state_no_duplicates():
    """State with 3 transactions; distinct amounts and dates, no duplicates."""
    transactions = [
        Transaction(
            id="tx-a",
            date="2026-01-02",
            amount_original=-10.0,
            amount_base=-10.0,
            currency="USD",
            merchant="SHOP A",
            account="Checking",
            source_file="data/a.xlsx",
        ),
        Transaction(
            id="tx-b",
            date="2026-01-05",
            amount_original=-99.0,
            amount_base=-99.0,
            currency="USD",
            merchant="SHOP B",
            account="Checking",
            source_file="data/a.xlsx",
        ),
        Transaction(
            id="tx-c",
            date="2026-01-10",
            amount_original=-5.0,
            amount_base=-5.0,
            currency="USD",
            merchant="SHOP C",
            account="Checking",
            source_file="data/a.xlsx",
        ),
    ]
    return _base_state(transactions)

def test_detect_duplicates_exact_match_pairs_without_llm(
    config, state_with_duplicates
):
    """tx1 and tx2 have same date and amount; they are detected as duplicates via exact match (no LLM call)."""
    detect_duplicates = make_detect_duplicates_node(config)
    result = detect_duplicates(state_with_duplicates)

    assert "transactions" in result
    transactions = result["transactions"]
    assert len(transactions) == 4

    assert "duplicates" in result
    duplicates = result["duplicates"]
    assert len(duplicates) == 2

    # tx-002 should be marked as duplicate of tx-001
    by_id = {t.id: t for t in transactions}
    assert by_id["tx-002"].duplicate_of == "tx-001"


def test_detect_duplicates_no_duplicates_when_all_distinct(config, state_no_duplicates):
    """When no pair matches (different amounts/dates), duplicates list stays empty."""
    detect_duplicates = make_detect_duplicates_node(config)
    result = detect_duplicates(state_no_duplicates)

    assert "transactions" in result
    assert len(result["transactions"]) == 3
    assert result["duplicates"] == []

    for t in result["transactions"]:
        assert t.duplicate_of is None
