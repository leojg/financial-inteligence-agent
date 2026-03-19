"""Node functions for the chat LangGraph pipeline."""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from agents.chat.configuration import ChatConfig
from agents.chat.state import ChatState
from shared.repositories.database_repository import (
    DatabaseRepository as DatabaseService,
)


def load_context(state: ChatState) -> dict[str, Any]:
    """Load financial context from the latest full-range insights cache."""
    database_service = DatabaseService()

    date_range = database_service.get_transaction_date_range(None)
    if date_range is None:
        raise ValueError("No transactions found in the database.")
    date_from, date_to = date_range

    insights_cache = database_service.get_insights_cache(date_from, date_to, None)

    if insights_cache is None:
        raise ValueError("No insights found in the database.")

    goals = database_service.get_active_goals()
    goals_prompt = (
        "The user has defined the following financial goals.\n"
        + "\n".join(f"- {goal['content']}" for goal in goals)
    ) if goals else None

    # Trimming out larger aggregations to keep token size smaller
    aggregations = insights_cache["aggregations"]

    # Trim transfer fees, keep totals, drop individual transactions
    fees = aggregations.get("transfer_fees_summary")
    if isinstance(fees, dict):
        aggregations["transfer_fees_summary"] = {
            "count": fees.get("count"),
            "total": fees.get("total"),
            "avg_per_transfer": fees.get("avg_per_transfer"),
        }

    aggregations.pop("receipt_line_breakdown", None)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "accounts": None,
        "aggregations": aggregations,
        "habits": insights_cache["habits"],
        "suggestions": insights_cache["suggestions"],
        "goals_prompt": goals_prompt,
    }

def make_chat_node(config: ChatConfig, tools: list[Any]) -> Callable[[ChatState], dict[str, Any]]:
    """Return a chat node function bound to the given config and tools."""
    llm = ChatOpenAI(model=config.model_name, temperature=config.temperature)
    llm_with_tools = llm.bind_tools(tools)

    def chat(state: ChatState) -> dict[str, Any]:

        date_from = state["date_from"]
        date_to = state["date_to"]
        goals_prompt = state["goals_prompt"]
        aggregations = state["aggregations"]
        habits = state["habits"]
        suggestions = state["suggestions"]

        prompt = f"""
            You are a personal finance analyst.
            You have access to the user's financial data from {date_from} to {date_to}.

            {goals_prompt if goals_prompt else ""}

            Below is a summary of the user's spending data and insights.
            Use this to answer questions directly when possible, 
            without calling tools.

            Aggregations:
            {json.dumps(aggregations, indent=2)}

            Spending habits:
            {json.dumps(habits, indent=2)}

            Observations:
            {json.dumps(suggestions, indent=2)}

            When the user asks for more detail — specific merchants, 
            individual transactions, category trends — use the available 
            tools to drill down.

            Always use the full date range ({date_from} to {date_to}) 
            unless the user explicitly mentions a specific time period.
        """

        result = llm_with_tools.invoke(
            [SystemMessage(content=prompt), *state["messages"]]
        )

        return {
            "messages": [result],
        }

    return chat