"""Insights service — invokes the insights agent and exposes cached results."""

from __future__ import annotations

import logging

from agents.insights.configuration import DEFAULT_CONFIG, InsightsConfig
from agents.insights.graph import make_graph
from agents.insights.state import initial_insights_state
from services.schemas import InsightsResponse
from shared.repositories.database_repository import DatabaseRepository

logger = logging.getLogger(__name__)

def run(
    date_from: str | None = None,
    date_to: str | None = None,
    accounts: list[str] | None = None,
    force_recompute: bool = False,
    config: InsightsConfig = DEFAULT_CONFIG,
) -> InsightsResponse:
    """Run the insights pipeline and return the result.

    If a valid cache exists and force_recompute=False, the pipeline returns
    immediately from cache without re-running LLM calls.
    """
    graph = make_graph(config, checkpointer=None)
    initial = initial_insights_state(
        date_from=date_from,
        date_to=date_to,
        accounts=accounts,
        force_recompute=force_recompute,
    )
    try:
        result = graph.invoke(initial)
        return InsightsResponse(
            date_from=result.get("date_from"),
            date_to=result.get("date_to"),
            aggregations=result.get("aggregations"),
            habits=result.get("habits") or [],
            suggestions=result.get("suggestions") or [],
        )
    except ValueError as e:
        logger.error(f"Insights failed: {e}")
        raise


def get_latest(accounts: list[str] | None = None) -> InsightsResponse | None:
    """Return the most recent cached insights, or None if no cache exists."""
    repo = DatabaseRepository()

    cached = repo.get_latest_insights_cache()
    if cached is None:
        return None
    return InsightsResponse(
        date_from=cached["date_from"],
        date_to=cached["date_to"],
        aggregations=cached["aggregations"],
        habits=cached.get("habits") or [],
        suggestions=cached.get("suggestions") or [],
    )
