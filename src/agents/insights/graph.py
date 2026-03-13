"""LangGraph insights graph definition."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.insights.configuration import DEFAULT_CONFIG, InsightsConfig
from agents.insights.state import InsightsState


def make_graph(
    config: InsightsConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the insights StateGraph with optional checkpointer."""
    graph = StateGraph(InsightsState)

    graph.add_edge(START, END)

    return graph.compile(checkpointer=checkpointer)


graph = make_graph(checkpointer=None)
