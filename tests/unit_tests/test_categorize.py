"""Unit tests for the categorize node."""

import json
from unittest.mock import patch

import pytest

from agent.configuration import ReconciliationConfig, DEFAULT_CONFIG
from agent.nodes import make_categorize_node
from agent.state import Transaction


@pytest.fixture
def config():
    return DEFAULT_CONFIG


@pytest.fixture
def two_transactions():
    """Two transactions without category (as produced by normalize/convert_currency)."""
    return [
        Transaction(
            id="tx-001",
            date="2026-01-02",
            amount_original=-50.0,
            amount_base=-50.0,
            currency="USD",
            merchant="SUPERMARKET",
            account="Checking",
            source_file="data/account.xlsx",
        ),
        Transaction(
            id="tx-002",
            date="2026-01-03",
            amount_original=-25.0,
            amount_base=-25.0,
            currency="USD",
            merchant="COFFEE SHOP",
            account="Checking",
            source_file="data/account.xlsx",
        ),
    ]


@pytest.fixture
def state_with_transactions(two_transactions):
    return {
        "source_folder": "data",
        "raw_documents": [],
        "transactions": two_transactions,
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }


def test_categorize_assigns_categories_from_llm_response(
    config, state_with_transactions
):
    """LLM returns valid categories; transactions get them and no needs_review."""
    mock_response = [
        {"id": "tx-001", "category": "Groceries"},
        {"id": "tx-002", "category": "Dining"},
    ]
    with patch("agent.nodes.ChatOpenAI") as MockLLM:
        mock_llm_instance = MockLLM.return_value
        mock_llm_instance.invoke.return_value.content = json.dumps(mock_response)
        categorize = make_categorize_node(config)
        result = categorize(state_with_transactions)

    assert "transactions" in result
    out = result["transactions"]
    assert len(out) == 2
    assert out[0].category == "Groceries"
    assert out[0].needs_review is False
    assert out[1].category == "Dining"
    assert out[1].needs_review is False


def test_categorize_marks_needs_review_when_llm_returns_null(
    config, state_with_transactions
):
    """LLM returns null for one transaction; that one gets needs_review."""
    mock_response = [
        {"id": "tx-001", "category": "Groceries"},
        {"id": "tx-002", "category": None},
    ]
    with patch("agent.nodes.ChatOpenAI") as MockLLM:
        mock_llm_instance = MockLLM.return_value
        mock_llm_instance.invoke.return_value.content = json.dumps(mock_response)
        categorize = make_categorize_node(config)
        result = categorize(state_with_transactions)

    out = result["transactions"]
    assert out[0].category == "Groceries"
    assert out[0].needs_review is False
    assert out[1].category is None
    assert out[1].needs_review is True
    assert "confidently" in (out[1].review_reason or "")


def test_categorize_marks_batch_needs_review_on_json_error(
    config, state_with_transactions
):
    """LLM returns invalid JSON; batch is marked needs_review with batch failed reason."""
    with patch("agent.nodes.ChatOpenAI") as MockLLM:
        mock_llm_instance = MockLLM.return_value
        mock_llm_instance.invoke.return_value.content = "not valid json"
        categorize = make_categorize_node(config)
        result = categorize(state_with_transactions)

    out = result["transactions"]
    assert len(out) == 2
    for t in out:
        assert t.needs_review is True
        assert t.review_reason == "Categorization batch failed"
