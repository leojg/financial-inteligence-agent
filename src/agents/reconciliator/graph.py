"""LangGraph reconciliation graph definition."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from agents.reconciliator.configuration import DEFAULT_CONFIG, ReconciliationConfig
from agents.reconciliator.nodes import (
    generate_report,
    human_review,
    ingest,
    make_categorize_node,
    make_convert_currency_node,
    make_detect_duplicates_node,
    make_flag_suspicious_node,
    make_ingest_images_node,
    make_normalize_node,
    make_review_low_confidence_transactions_node,
    passthrough,
    prepare_ingest,
)
from agents.reconciliator.state import ReconciliationState


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


def _make_has_low_confidence(threshold: float) -> Callable[[ReconciliationState], str]:
    """Return a conditional edge fn: only route to review when there are low-confidence transactions."""

    def _has_low_confidence(state: ReconciliationState) -> str:
        transactions = state.get("transactions") or []
        for t in transactions:
            conf = (
                t.get("confidence")
                if isinstance(t, dict)
                else getattr(t, "confidence", None)
            )
            if conf is not None and conf < threshold:
                return "review_low_confidence_transactions"
        return "convert_currency"

    return _has_low_confidence


def make_graph(
    config: ReconciliationConfig = DEFAULT_CONFIG,
    checkpointer: Any = None,
    interrupt_before: list[str] | None = None,
) -> Any:
    """Build and compile the reconciliation StateGraph with optional checkpointer."""
    # Default to the two review nodes if not explicitly set
    if interrupt_before is None:
        interrupt_before = ["review_low_confidence_transactions", "human_review"]

    graph = StateGraph(ReconciliationState)

    graph.add_node("prepare_ingest", prepare_ingest)
    graph.add_node("ingest", ingest)
    graph.add_node("ingest_images", make_ingest_images_node(config))  # type: ignore[arg-type]
    graph.add_node("normalize", make_normalize_node(config))  # type: ignore[arg-type]
    graph.add_node(
        "review_low_confidence_transactions",
        make_review_low_confidence_transactions_node(config),  # type: ignore[arg-type]
    )
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
        {"ingest": "ingest", "skip_documents": "skip_documents"},
    )
    graph.add_conditional_edges(
        "prepare_ingest",
        _has_images,
        {"ingest_images": "ingest_images", "skip_images": "skip_images"},
    )
    graph.add_edge("ingest", "normalize")
    graph.add_edge("ingest_images", "normalize")
    graph.add_edge("skip_images", "normalize")
    graph.add_edge("skip_documents", "normalize")

    graph.add_conditional_edges(
        "normalize",
        _make_has_low_confidence(config.image_low_confidence_threshold),
        {
            "review_low_confidence_transactions": "review_low_confidence_transactions",
            "convert_currency": "convert_currency",
        },
    )
    graph.add_edge("review_low_confidence_transactions", "convert_currency")

    graph.add_edge("convert_currency", "categorize")

    graph.add_edge("categorize", "detect_duplicates")
    graph.add_edge("categorize", "flag_suspicious")

    graph.add_edge("detect_duplicates", "human_review")
    graph.add_edge("flag_suspicious", "human_review")

    graph.add_edge("human_review", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )


graph = make_graph(checkpointer=None)
