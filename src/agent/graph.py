"""LangGraph reconciliation graph definition."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.configuration import DEFAULT_CONFIG, ReconciliationConfig
from agent.db import get_checkpointer
from agent.nodes import (
    generate_report,
    human_review,
    ingest,
    make_ingest_images_node,
    make_categorize_node,
    make_convert_currency_node,
    make_detect_duplicates_node,
    make_flag_suspicious_node,
    make_normalize_node,
    passthrough,
)
from agent.state import ReconciliationState


def make_graph(
    config: ReconciliationConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the reconciliation StateGraph with optional checkpointer."""
    graph = StateGraph(ReconciliationState)

    graph.add_node("ingest", ingest)
    graph.add_node("ingest_images", make_ingest_images_node(config))
    graph.add_node("normalize", make_normalize_node(config))
    graph.add_node("convert_currency", make_convert_currency_node(config))  # type: ignore[arg-type]
    graph.add_node("categorize", make_categorize_node(config))  # type: ignore[arg-type]
    graph.add_node("detect_duplicates", make_detect_duplicates_node(config))  # type: ignore[arg-type]
    graph.add_node("flag_suspicious", make_flag_suspicious_node(config))  # type: ignore[arg-type]
    graph.add_node("human_review", human_review)
    graph.add_node("generate_report", generate_report)
    graph.add_node("initialize_ingest", passthrough)

    graph.add_edge(START, "initialize_ingest")
    #graph.add_edge("initialize_ingest", "ingest")
    graph.add_edge("initialize_ingest", "ingest_images")
    graph.add_edge("ingest", "normalize")
    graph.add_edge("ingest_images", "normalize")
    graph.add_edge("normalize", "convert_currency")
    graph.add_edge("convert_currency", "categorize")
    graph.add_edge("categorize", "detect_duplicates")
    graph.add_edge("categorize", "flag_suspicious")
    graph.add_edge("detect_duplicates", "human_review")
    graph.add_edge("flag_suspicious", "human_review")
    graph.add_edge("human_review", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review"]
    )

graph = make_graph(checkpointer=get_checkpointer())