"""State types for the chat graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    """State for the chat ReAct graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    conversation_id: str

    # Resolved by load_context — always full range
    date_from: str | None
    date_to: str | None
    accounts: list[str] | None

    # Financial context — full-range insights
    aggregations: dict[str, Any] | None
    habits: list[dict[str, Any]] | None
    suggestions: list[dict[str, Any]] | None
    goals_prompt: str | None


def initial_chat_state(
    conversation_id: str,
    messages: list[AnyMessage] | None = None,
) -> ChatState:
    """Return a blank ChatState for a new conversation."""
    return {
        "messages": messages or [],
        "conversation_id": conversation_id,
        "date_from": None,
        "date_to": None,
        "accounts": None,
        "aggregations": None,
        "habits": None,
        "suggestions": None,
        "goals_prompt": None,
    }
