"""Chat service — invokes the chat ReAct agent for a single conversational turn."""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage

from agents.chat.configuration import DEFAULT_CONFIG, ChatConfig
from agents.chat.graph import make_graph
from services.schemas import ChatResponse


def send_message(
    message: str,
    conversation_id: str | None = None,
    config: ChatConfig = DEFAULT_CONFIG,
) -> ChatResponse:
    """Send a message to the chat agent and return (response, conversation_id).

    The conversation_id is used as the checkpointer thread_id so history
    accumulates across calls. Pass None to start a new conversation.

    Context (aggregations, goals) is loaded by the graph's load_context node
    from the latest insights cache — callers do not need to pass it.

    Returns:
        (reply, conversation_id) — conversation_id is either the one passed in
        or a freshly generated UUID for new conversations.
    """
    conversation_id = conversation_id or str(uuid.uuid4())
    graph = make_graph(config)
    result = graph.invoke({"messages": [HumanMessage(content=message)], "conversation_id": conversation_id})

    last = result["messages"][-1]
    reply: str = last.content if hasattr(last, "content") else str(last)
    return ChatResponse(response=reply, conversation_id=conversation_id)
