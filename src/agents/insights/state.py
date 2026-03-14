"""State types for the insights graph."""

from __future__ import annotations

from typing import Any, TypedDict


class InsightsState(TypedDict):
    """Graph state for the insights agent."""
    date_from: str | None          # resolved to earliest transaction date if None
    date_to: str | None            # resolved to latest transaction date if None
    accounts: list[str] | None     # None means all accounts
    force_recompute: bool          # bypass insights_cache and re-run pipeline
    cache_valid: bool
    goals_prompt: str | None
    aggregations: dict[str, Any] | None
    habits: list[dict[str, Any]] | None
    suggestions: list[dict[str, Any]] | None

def initial_insights_state(
    date_from: str | None = None,
    date_to: str | None = None,
    accounts: list[str] | None = None,
    force_recompute: bool = False,
) -> InsightsState: