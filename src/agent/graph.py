"""LangGraph reconciliation graph definition."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from agent.configuration import DEFAULT_CONFIG, ReconciliationConfig
from agent.nodes import (
    generate_report,
    human_review,
    prepare_ingest,
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

def _has_images(state: ReconciliationState) -> str:
    """Return 'ingest_images' if source_files has image paths, else 'skip_images'."""
    exts = (".png", ".jpg", ".jpeg")
    source_files = state["source_files"]
    for path in source_files:
        if Path(path).suffix.lower() in exts:
            return "ingest_images"
    return "skip_images"

def _has_documents(state: ReconciliationState) -> str:
    """Return 'ingest' if source_files has document paths, else 'skip_documents'."""
    exts = (".csv", ".xlsx", ".xls", ".pdf")
    source_files = state["source_files"]
    for path in source_files:
        if Path(path).suffix.lower() in exts:
            return "ingest"
    return "skip_documents"

def make_graph(
    config: ReconciliationConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the reconciliation StateGraph with optional checkpointer."""
    graph = StateGraph(ReconciliationState)

    graph.add_node("prepare_ingest", prepare_ingest)
    graph.add_node("ingest", ingest)
    graph.add_node("ingest_images", make_ingest_images_node(config))
    graph.add_node("normalize", make_normalize_node(config))
    graph.add_node("convert_currency", make_convert_currency_node(config))  # type: ignore[arg-type]
    graph.add_node("categorize", make_categorize_node(config))  # type: ignore[arg-type]
    graph.add_node("detect_duplicates", make_detect_duplicates_node(config))  # type: ignore[arg-type]
    graph.add_node("flag_suspicious", make_flag_suspicious_node(config))  # type: ignore[arg-type]
    graph.add_node("human_review", human_review)
    graph.add_node("generate_report", generate_report)
    graph.add_node("skip_images", passthrough)
    graph.add_node("skip_documents", passthrough)

    graph.add_edge(START, "prepare_ingest")
    graph.add_conditional_edges(
        "prepare_ingest",
        _has_documents,
        {
            "ingest": "ingest",
            "skip_documents": "skip_documents"
        }
    )
    graph.add_conditional_edges(
        "prepare_ingest",
        _has_images,
        {
            "ingest_images": "ingest_images",
            "skip_images": "skip_images"
        }
    )
    graph.add_edge("ingest", "normalize")
    graph.add_edge("ingest_images", "normalize")
    graph.add_edge("skip_images", "normalize")
    graph.add_edge("skip_documents", "normalize")

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

graph = make_graph(checkpointer=None)