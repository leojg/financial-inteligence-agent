from __future__ import annotations
import json
from typing import Callable
from langchain_openai import ChatOpenAI
from shared.services.database_service import DatabaseService
from agents.insights.state import InsightsState
from agents.insights.configuration import InsightsConfig
from shared.models import InsightsOutput
from agents.insights.tools import get_spending_by_category, get_month_over_month_deltas, get_recurring_charges, get_transfer_fees_summary

def load_context(state: InsightsState) -> InsightsState:
    """Load the context for the insights agent."""
    database_service = DatabaseService()

    accounts = state["accounts"]

    if not state.get("date_from") or not state.get("date_to"):
        date_range = database_service.get_transaction_date_range(accounts)
        if date_range is None:
            raise ValueError("No transactions found in the database.")
        date_from, date_to = date_range
    else:
        date_from, date_to = state["date_from"], state["date_to"]

    goals = database_service.get_active_goals()

    goals_prompt = (
        "The user has defined the following financial goals.\n"
        "Take them into consideration when making observations or flagging habits:\n"
        + "\n".join(f"- {goal['content']}" for goal in goals)
    ) if goals else None

    last_run = database_service.get_latest_reconciliation_run_date()
    last_insights = database_service.get_insights_cache(date_from, date_to, accounts)

    cache_is_stale = (
        last_insights is None
        or last_run is None
        or last_run > last_insights["created_at"]
    ) 
    
    cache_valid = not cache_is_stale and not state["force_recompute"]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "goals_prompt": goals_prompt,
        "cache_valid": cache_valid,
        "aggregations": last_insights["aggregations"] if cache_valid else None,
        "habits": last_insights["habits"] if cache_valid else None,
        "suggestions": last_insights["suggestions"] if cache_valid else None,

    }

def compute_aggregations(state: InsightsState) -> InsightsState:
    """Compute aggregated spending data from the transactions table."""
    date_from, date_to = state["date_from"], state["date_to"]
    accounts = state["accounts"]

    spending_by_category = get_spending_by_category.invoke(
        {
            "date_from": date_from,
            "date_to": date_to,
            "accounts": accounts,
        }
    )
    month_deltas = get_month_over_month_deltas.invoke(
        {
            "date_from": date_from,
            "date_to": date_to,
            "accounts": accounts,
        }
    )
    recurring_charges = get_recurring_charges.invoke(
        {
            "date_from": date_from,
            "date_to": date_to,
            "accounts": accounts,
        }
    )
    transfer_fees_summary = get_transfer_fees_summary.invoke(
        {
            "date_from": date_from,
            "date_to": date_to,
            "accounts": accounts,
        }
    )

    return {
        "aggregations": {
            "spending_by_category": spending_by_category,
            "month_deltas": month_deltas,
            "recurring_charges": recurring_charges,
            "transfer_fees_summary": transfer_fees_summary,
        },
    }

def make_generate_insights_node(config: InsightsConfig) -> Callable[[InsightsState], InsightsState]:
    """Return a node that generates insights from the aggregated data."""

    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature).with_structured_output(InsightsOutput)


    def generate_insights(state: InsightsState) -> InsightsState:
        """Generate insights from the aggregated data."""

        goals_section = f"\n{state['goals_prompt']}\n" if state.get("goals_prompt") else ""

        prompt = f"""You are a personal finance analyst.
            Analyze the spending data below and return observations about the user's financial habits
            and actionable suggestions for improvement.

            Period: {state["date_from"]} to {state["date_to"]}
            {goals_section}

            Spending data:
            {json.dumps(state["aggregations"], indent=2)}

            For habits: only include patterns that are either notably positive (worth reinforcing) or concerning (worth addressing). Skip neutral observations.
            For suggestions: provide specific, actionable recommendations with concrete numbers from the data.
            Severity guide: info = noteworthy, warning = needs attention, critical = requires immediate action.
        """

        result: InsightsOutput = llm.invoke(prompt)

        return {
            "habits": [h.model_dump() for h in result.habits],
            "suggestions": [s.model_dump() for s in result.suggestions],
        }

    return generate_insights

def persist_results(state: InsightsState) -> dict:
    """Persist aggregations, habits and suggestions to the insights cache."""
    DatabaseService().save_insights_cache(
        date_from=state["date_from"],
        date_to=state["date_to"],
        accounts=state["accounts"],
        aggregations=state["aggregations"],
        habits=state["habits"],
        suggestions=state["suggestions"],
    )
    return {}