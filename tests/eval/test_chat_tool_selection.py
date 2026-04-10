"""Chat agent tool-selection eval (v1.4 §4.1): assert expected tools are invoked.

§4.2 (response faithfulness) is intentionally omitted."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.chat.configuration import DEFAULT_CONFIG
from agents.chat.graph import make_graph
from agents.chat.state import ChatState


def _fake_load_context(_state: ChatState) -> dict[str, Any]:
    """Insights-shaped context without requiring a seeded DB for load_context."""
    return {
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "accounts": None,
        "aggregations": {
            "spending_by_category": [
                {"category": "Groceries", "total": 350.0},
                {"category": "Dining", "total": 280.0},
            ],
            "month_deltas": [
                {
                    "month": "2026-01",
                    "total": 900.0,
                    "delta_pct": 5.0,
                    "avg_baseline": 850.0,
                },
            ],
            "recurring_charges": [
                {
                    "merchant_normalized": "netflix",
                    "months_seen": 2,
                    "avg_amount": 15.0,
                    "cv": 0.01,
                }
            ],
            "transfer_fees_summary": [],
        },
        "habits": [
            {
                "category": "Groceries",
                "observation": "Groceries are a large share of spend.",
                "severity": "info",
            }
        ],
        "suggestions": [
            {
                "type": "budget",
                "title": "Track dining",
                "body": "Review restaurant line items.",
                "severity": "info",
            }
        ],
        "goals_prompt": None,
    }


def _collect_tool_names(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for tc in getattr(m, "tool_calls", None) or []:
            if isinstance(tc, dict):
                n = tc.get("name")
            else:
                n = getattr(tc, "name", None)
            if n:
                names.append(str(n))
    return names


def _multiset_missing(expected: list[str], found: list[str]) -> list[str]:
    pool = list(found)
    missing: list[str] = []
    for name in sorted(expected):
        try:
            pool.pop(pool.index(name))
        except ValueError:
            missing.append(name)
    return missing


def _run_chat(question: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # `agents.chat.graph` as a dotted path resolves to the compiled graph exported from
    # agents.chat.__init__, not the graph.py module — patch the real module object.
    chat_graph_mod = importlib.import_module("agents.chat.graph")
    monkeypatch.setattr(chat_graph_mod, "load_context", _fake_load_context)
    graph = make_graph(DEFAULT_CONFIG, checkpointer=None)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=question)],
            "conversation_id": "eval-chat-tool-selection",
        },
        {"recursion_limit": 25},
    )
    return _collect_tool_names(result.get("messages") or [])


# Prompts nudge tool use; the system prompt otherwise answers from context without tools.
TOOL_SELECTION_CASES: list[tuple[str, list[str]]] = [
    (
        "Use tools to fetch spending totals broken down by category for the full period.",
        ["get_spending_by_category"],
    ),
    (
        "Call the recurring-charges tool and list every recurring subscription merchant.",
        ["get_recurring_charges"],
    ),
    (
        "Use the merchant drill-down tool for transactions matching merchant Netflix.",
        ["get_transactions_by_merchant"],
    ),
    (
        "Invoke month-over-month deltas for overall spending for the date range.",
        ["get_month_over_month_deltas"],
    ),
    (
        "Use the transfer fees summary tool to summarize transfer and fee charges.",
        ["get_transfer_fees_summary"],
    ),
    (
        "Call receipt line breakdown for the period.",
        ["get_receipt_line_breakdown"],
    ),
    (
        "Which merchants had the highest total spend? Use the top merchants tool.",
        ["get_top_merchants_by_amount"],
    ),
    (
        "Show the category trend tool output for the Dining category.",
        ["get_category_trend"],
    ),
    (
        "Use the account summary tool for an overview of accounts.",
        ["get_account_summary"],
    ),
    (
        "List the largest individual transactions using the appropriate tool.",
        ["get_largest_transactions"],
    ),
    (
        "Do not use the spending figures in the prompt above — call get_spending_by_category "
        "exactly twice: one call for Groceries and one call for Dining.",
        ["get_spending_by_category", "get_spending_by_category"],
    ),
    ("What is the weather in Paris today?", []),
    ("Write a professional email to my boss asking for a raise.", []),
]


@pytest.mark.eval
@pytest.mark.parametrize("question,expected_tools", TOOL_SELECTION_CASES)
def test_chat_tool_selection(question: str, expected_tools: list[str], monkeypatch):
    """Assert the chat run includes the expected tool name(s) (multiset, order ignored)."""
    found = _run_chat(question, monkeypatch)
    if not expected_tools:
        assert not found, f"Expected no tool calls, got: {found}"
        return
    missing = _multiset_missing(expected_tools, found)
    assert not missing, (
        f"Missing expected tool(s) {missing}. Found: {found}. Question: {question!r}"
    )
