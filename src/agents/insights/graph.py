"""LangGraph insights graph definition."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.insights.configuration import DEFAULT_CONFIG, InsightsConfig
from agents.insights.nodes import (
    compute_aggregations,
    load_context,
    make_generate_insights_node,
    persist_results,
)
from agents.insights.state import InsightsState


def _should_recompute(state: InsightsState) -> str:
    """Return the next node to execute based on the cache validity."""
    return "compute_aggregations" if not state["cache_valid"] else END

def make_graph(
    config: InsightsConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the insights StateGraph with optional checkpointer."""
    graph = StateGraph(InsightsState)

    graph.add_node("load_context", load_context)
    graph.add_node("compute_aggregations", compute_aggregations)
    graph.add_node("generate_insights", make_generate_insights_node(config))  # type: ignore[arg-type]
    graph.add_node("persist_results", persist_results)  


    graph.add_edge(START, "load_context")
    graph.add_conditional_edges("load_context", _should_recompute)

    graph.add_edge("compute_aggregations", "generate_insights")
    graph.add_edge("generate_insights", "persist_results")
    graph.add_edge("persist_results", END)

    return graph.compile(checkpointer=checkpointer)


graph = make_graph(checkpointer=None)
