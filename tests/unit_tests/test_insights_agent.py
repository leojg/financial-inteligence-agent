"""Unit tests for the insights agent — nodes, routing, and tools.

All tests mock the LLM and DatabaseService; no real API calls or DB writes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.insights.configuration import DEFAULT_CONFIG
from agents.insights.graph import _should_recompute, make_graph
from agents.insights.nodes import (
    compute_aggregations,
    load_context,
    make_generate_insights_node,
    persist_results,
)
from agents.insights.state import InsightsState, initial_insights_state
from agents.insights.tools import get_month_over_month_deltas, get_recurring_charges
from shared.models import Habit, InsightsOutput, Suggestion

# ── Helpers ───────────────────────────────────────────────────────────────────


def _base_state(**overrides: Any) -> InsightsState:
    state = initial_insights_state()
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _mock_db(
    *,
    date_range: tuple[str, str] | None = ("2026-01-01", "2026-01-31"),
    goals: list[dict[str, Any]] | None = None,
    last_run_date: str | None = None,
    insights_cache: dict[str, Any] | None = None,
) -> MagicMock:
    db = MagicMock()
    db.get_transaction_date_range.return_value = date_range
    db.get_active_goals.return_value = goals or []
    db.get_latest_reconciliation_run_date.return_value = last_run_date
    db.get_insights_cache.return_value = insights_cache
    return db


# ── Graph routing ─────────────────────────────────────────────────────────────


class TestShouldRecompute:
    """_should_recompute routes based on cache_valid flag."""

    def test_routes_to_compute_when_cache_invalid(self) -> None:
        state = _base_state(cache_valid=False)
        assert _should_recompute(state) == "compute_aggregations"

    def test_routes_to_end_when_cache_valid(self) -> None:
        from langgraph.graph import END

        state = _base_state(cache_valid=True)
        assert _should_recompute(state) == END

    def test_graph_has_required_nodes(self) -> None:
        g = make_graph(checkpointer=None)
        assert "load_context" in g.nodes
        assert "compute_aggregations" in g.nodes
        assert "generate_insights" in g.nodes
        assert "persist_results" in g.nodes


# ── load_context ──────────────────────────────────────────────────────────────


class TestLoadContext:
    """load_context resolves dates, goals, staleness, and cache."""

    def test_uses_db_date_range_when_state_dates_are_none(self) -> None:
        db = _mock_db(date_range=("2026-01-01", "2026-01-31"))
        state = _base_state(date_from=None, date_to=None)

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["date_from"] == "2026-01-01"
        assert result["date_to"] == "2026-01-31"

    def test_uses_state_dates_when_provided(self) -> None:
        db = _mock_db()
        state = _base_state(date_from="2026-02-01", date_to="2026-02-28")

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["date_from"] == "2026-02-01"
        assert result["date_to"] == "2026-02-28"
        db.get_transaction_date_range.assert_not_called()

    def test_raises_when_no_transactions(self) -> None:
        db = _mock_db(date_range=None)
        state = _base_state(date_from=None, date_to=None)

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            with pytest.raises(ValueError, match="No transactions"):
                load_context(state)

    def test_goals_prompt_injected_when_goals_exist(self) -> None:
        db = _mock_db(goals=[{"content": "Save $1000/month"}])
        state = _base_state()

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["goals_prompt"] is not None
        assert "Save $1000/month" in result["goals_prompt"]

    def test_goals_prompt_is_none_when_no_goals(self) -> None:
        db = _mock_db(goals=[])
        state = _base_state()

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["goals_prompt"] is None

    def test_cache_valid_when_cache_exists_and_run_is_older(self) -> None:
        cache = {
            "aggregations": {"spending_by_category": []},
            "habits": [],
            "suggestions": [],
            "created_at": "2026-01-20T10:00:00",
        }
        db = _mock_db(last_run_date="2026-01-15T08:00:00", insights_cache=cache)
        state = _base_state(force_recompute=False)

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["cache_valid"] is True
        assert result["aggregations"] == cache["aggregations"]
        assert result["habits"] == cache["habits"]

    def test_cache_invalid_when_run_is_newer_than_cache(self) -> None:
        cache = {
            "aggregations": {},
            "habits": [],
            "suggestions": [],
            "created_at": "2026-01-10T08:00:00",
        }
        db = _mock_db(last_run_date="2026-01-20T10:00:00", insights_cache=cache)
        state = _base_state(force_recompute=False)

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["cache_valid"] is False
        assert result["aggregations"] is None

    def test_cache_invalid_when_force_recompute(self) -> None:
        cache = {
            "aggregations": {},
            "habits": [],
            "suggestions": [],
            "created_at": "2026-01-20T10:00:00",
        }
        db = _mock_db(last_run_date="2026-01-15T08:00:00", insights_cache=cache)
        state = _base_state(force_recompute=True)

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["cache_valid"] is False

    def test_cache_invalid_when_no_cache(self) -> None:
        db = _mock_db(last_run_date="2026-01-15T08:00:00", insights_cache=None)
        state = _base_state()

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = load_context(state)

        assert result["cache_valid"] is False


# ── compute_aggregations ──────────────────────────────────────────────────────


class TestComputeAggregations:
    """compute_aggregations calls all five tool functions and assembles the dict."""

    def _run(self, db: MagicMock) -> dict[str, Any]:
        state = _base_state(date_from="2026-01-01", date_to="2026-01-31", accounts=None)
        with patch("agents.insights.tools.DatabaseService", return_value=db):
            return compute_aggregations(state)

    def _make_db(self) -> MagicMock:
        db = MagicMock()
        db.get_spending_by_category.return_value = [
            {"category": "Food", "total": 200.0}
        ]
        db.get_monthly_spend.return_value = [{"month": "2026-01", "total": 500.0}]
        db.get_merchant_monthly_spend.return_value = []
        db.get_transfer_fees_summary.return_value = []
        db.get_receipt_line_breakdown.return_value = []
        return db

    def test_returns_all_aggregation_keys(self) -> None:
        result = self._run(self._make_db())
        agg = result["aggregations"]
        assert "spending_by_category" in agg
        assert "month_deltas" in agg
        assert "recurring_charges" in agg
        assert "transfer_fees_summary" in agg
        assert "receipt_line_breakdown" in agg

    def test_spending_by_category_passed_through(self) -> None:
        result = self._run(self._make_db())
        assert result["aggregations"]["spending_by_category"] == [
            {"category": "Food", "total": 200.0}
        ]

    def test_all_db_methods_called(self) -> None:
        db = self._make_db()
        self._run(db)
        db.get_spending_by_category.assert_called_once()
        db.get_monthly_spend.assert_called_once()
        db.get_merchant_monthly_spend.assert_called_once()
        db.get_transfer_fees_summary.assert_called_once()
        db.get_receipt_line_breakdown.assert_called_once()


# ── generate_insights ─────────────────────────────────────────────────────────


class TestGenerateInsights:
    """make_generate_insights_node passes aggregations to the LLM and returns habits/suggestions."""

    def _run(
        self, llm_output: InsightsOutput, **state_overrides: Any
    ) -> dict[str, Any]:
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_llm
        mock_llm.invoke.return_value = llm_output

        state = _base_state(
            date_from="2026-01-01",
            date_to="2026-01-31",
            aggregations={
                "spending_by_category": [{"category": "Food", "total": 200.0}]
            },
            goals_prompt=None,
            **state_overrides,
        )

        with patch("agents.insights.nodes.ChatOpenAI", return_value=mock_llm):
            node = make_generate_insights_node(DEFAULT_CONFIG)
            return node(state)

    def _make_output(self) -> InsightsOutput:
        return InsightsOutput(
            habits=[
                Habit(category="Food", observation="High spend", severity="warning")
            ],
            suggestions=[
                Suggestion(
                    type="spending",
                    title="Cook at home",
                    body="Reduce dining out",
                    severity="info",
                )
            ],
        )

    def test_returns_habits_and_suggestions(self) -> None:
        result = self._run(self._make_output())
        assert len(result["habits"]) == 1
        assert result["habits"][0]["category"] == "Food"
        assert len(result["suggestions"]) == 1
        assert result["suggestions"][0]["title"] == "Cook at home"

    def test_goals_injected_into_prompt_when_present(self) -> None:
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_llm
        mock_llm.invoke.return_value = self._make_output()

        state = _base_state(
            date_from="2026-01-01",
            date_to="2026-01-31",
            aggregations={},
            goals_prompt="Save $1000/month",
        )

        with patch("agents.insights.nodes.ChatOpenAI", return_value=mock_llm):
            node = make_generate_insights_node(DEFAULT_CONFIG)
            node(state)

        prompt = mock_llm.invoke.call_args[0][0]
        assert "Save $1000/month" in prompt

    def test_goals_absent_from_prompt_when_none(self) -> None:
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_llm
        mock_llm.invoke.return_value = self._make_output()

        state = _base_state(
            date_from="2026-01-01",
            date_to="2026-01-31",
            aggregations={},
            goals_prompt=None,
        )

        with patch("agents.insights.nodes.ChatOpenAI", return_value=mock_llm):
            node = make_generate_insights_node(DEFAULT_CONFIG)
            node(state)

        prompt = mock_llm.invoke.call_args[0][0]
        assert "Save $1000" not in prompt


# ── persist_results ───────────────────────────────────────────────────────────


class TestPersistResults:
    """persist_results calls save_insights_cache with the correct arguments."""

    def test_calls_save_insights_cache(self) -> None:
        db = MagicMock()
        state = _base_state(
            date_from="2026-01-01",
            date_to="2026-01-31",
            accounts=None,
            aggregations={"spending_by_category": []},
            habits=[{"category": "Food", "observation": "High", "severity": "warning"}],
            suggestions=[{"title": "Budget", "body": "Set limits", "severity": "info"}],
        )

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            persist_results(state)

        db.save_insights_cache.assert_called_once_with(
            date_from="2026-01-01",
            date_to="2026-01-31",
            accounts=None,
            aggregations={"spending_by_category": []},
            habits=state["habits"],
            suggestions=state["suggestions"],
        )

    def test_returns_empty_dict(self) -> None:
        db = MagicMock()
        state = _base_state(
            date_from="2026-01-01",
            date_to="2026-01-31",
            aggregations={},
            habits=[],
            suggestions=[],
        )

        with patch("agents.insights.nodes.DatabaseService", return_value=db):
            result = persist_results(state)

        assert result == {}


# ── Tools: business logic ─────────────────────────────────────────────────────


class TestGetMonthOverMonthDeltas:
    """Business logic: delta_pct and avg_baseline computed correctly."""

    def test_first_month_has_no_delta(self) -> None:
        db = MagicMock()
        db.get_monthly_spend.return_value = [{"month": "2026-01", "total": 500.0}]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_month_over_month_deltas.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-01-31"}
            )

        assert result[0]["delta_pct"] is None
        assert result[0]["avg_baseline"] is None

    def test_delta_pct_calculated_correctly(self) -> None:
        db = MagicMock()
        db.get_monthly_spend.return_value = [
            {"month": "2026-01", "total": 400.0},
            {"month": "2026-02", "total": 500.0},
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_month_over_month_deltas.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-02-28"}
            )

        assert result[1]["delta_pct"] == 25.0  # (500-400)/400 * 100

    def test_avg_baseline_uses_lookback_window(self) -> None:
        db = MagicMock()
        db.get_monthly_spend.return_value = [
            {"month": "2026-01", "total": 300.0},
            {"month": "2026-02", "total": 400.0},
            {"month": "2026-03", "total": 500.0},
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_month_over_month_deltas.invoke(
                {
                    "date_from": "2026-01-01",
                    "date_to": "2026-03-31",
                    "lookback_months": 2,
                }
            )

        # Month 3 baseline = avg of months 1 and 2 = (300+400)/2 = 350
        assert result[2]["avg_baseline"] == 350.0


class TestGetRecurringCharges:
    """Business logic: merchants with stable monthly amounts are flagged as recurring."""

    def test_single_month_merchant_excluded(self) -> None:
        db = MagicMock()
        db.get_merchant_monthly_spend.return_value = [
            {"merchant_normalized": "netflix", "month": "2026-01", "total": 15.0}
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_recurring_charges.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-01-31"}
            )

        assert result == []

    def test_stable_monthly_merchant_detected(self) -> None:
        db = MagicMock()
        db.get_merchant_monthly_spend.return_value = [
            {"merchant_normalized": "netflix", "month": "2026-01", "total": 15.0},
            {"merchant_normalized": "netflix", "month": "2026-02", "total": 15.0},
            {"merchant_normalized": "netflix", "month": "2026-03", "total": 15.0},
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_recurring_charges.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-03-31"}
            )

        assert len(result) == 1
        assert result[0]["merchant_normalized"] == "netflix"
        assert result[0]["avg_amount"] == 15.0
        assert result[0]["cv"] == 0.0

    def test_high_variance_merchant_excluded(self) -> None:
        """CV > 0.10 should not be flagged as recurring."""
        db = MagicMock()
        db.get_merchant_monthly_spend.return_value = [
            {"merchant_normalized": "amazon", "month": "2026-01", "total": 10.0},
            {"merchant_normalized": "amazon", "month": "2026-02", "total": 200.0},
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_recurring_charges.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-02-28"}
            )

        assert result == []

    def test_result_sorted_by_avg_amount_descending(self) -> None:
        db = MagicMock()
        db.get_merchant_monthly_spend.return_value = [
            {"merchant_normalized": "cheap", "month": "2026-01", "total": 5.0},
            {"merchant_normalized": "cheap", "month": "2026-02", "total": 5.0},
            {"merchant_normalized": "expensive", "month": "2026-01", "total": 100.0},
            {"merchant_normalized": "expensive", "month": "2026-02", "total": 100.0},
        ]

        with patch("agents.insights.tools.DatabaseService", return_value=db):
            result = get_recurring_charges.invoke(
                {"date_from": "2026-01-01", "date_to": "2026-02-28"}
            )

        assert result[0]["merchant_normalized"] == "expensive"
        assert result[1]["merchant_normalized"] == "cheap"


# ── Full graph: cache hit path ────────────────────────────────────────────────


class TestGraphCacheHitPath:
    """When cache is valid the graph should skip compute/generate/persist."""

    def test_cache_hit_skips_llm(self) -> None:
        cached = {
            "aggregations": {
                "spending_by_category": [{"category": "Food", "total": 200.0}]
            },
            "habits": [
                {"category": "Food", "observation": "High", "severity": "warning"}
            ],
            "suggestions": [
                {"title": "Budget", "body": "Set limits", "severity": "info"}
            ],
            "created_at": "2026-01-20T10:00:00",
        }
        db = _mock_db(last_run_date="2026-01-15T08:00:00", insights_cache=cached)

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_llm

        with (
            patch("agents.insights.nodes.DatabaseService", return_value=db),
            patch("agents.insights.nodes.ChatOpenAI", return_value=mock_llm),
        ):
            g = make_graph(checkpointer=None)
            result = g.invoke(initial_insights_state(force_recompute=False))

        mock_llm.invoke.assert_not_called()
        assert result["aggregations"] == cached["aggregations"]
        assert result["habits"] == cached["habits"]
