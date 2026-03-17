"""LangGraph chat graph definition."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agents.chat.configuration import DEFAULT_CONFIG, ChatConfig
from agents.chat.nodes import load_context, make_chat_node
from agents.chat.state import ChatState
from agents.insights.tools import (
    get_account_summary,
    get_category_trend,
    get_largest_transactions,
    get_month_over_month_deltas,
    get_receipt_line_breakdown,
    get_recurring_charges,
    get_spending_by_category,
    get_top_merchants_by_amount,
    get_transactions_by_merchant,
    get_transfer_fees_summary,
)

TOOLS = [
    # Aggregation tools (shared with insights pipeline)
    get_spending_by_category,
    get_month_over_month_deltas,
    get_recurring_charges,
    get_transfer_fees_summary,
    get_receipt_line_breakdown,
    # Narrow tools (chat-exclusive)
    get_top_merchants_by_amount,
    get_transactions_by_merchant,
    get_category_trend,
    get_account_summary,
    get_largest_transactions,
]


def _should_continue(state: ChatState) -> str:
    """Route to tools if the LLM made a tool call, otherwise end."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


def make_graph(
    config: ChatConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the chat StateGraph."""
    graph = StateGraph(ChatState)

    graph.add_node("load_context", load_context)
    graph.add_node("chat", make_chat_node(config, TOOLS))  # type: ignore[arg-type]
    graph.add_node("tools", ToolNode(TOOLS))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "chat")
    graph.add_conditional_edges("chat", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "chat")

    return graph.compile(checkpointer=checkpointer)


graph = make_graph(checkpointer=None)