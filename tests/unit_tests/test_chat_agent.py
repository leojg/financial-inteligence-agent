"""Unit tests for the chat agent — ReAct routing and system prompt construction.

All tests mock the LLM; no real API calls are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.chat.configuration import DEFAULT_CONFIG
from agents.chat.graph import _should_continue, make_graph
from agents.chat.nodes import make_chat_node
from agents.chat.state import ChatState

# ── Helpers ──────────────────────────────────────────────────────────────────


def _base_state(**overrides: Any) -> ChatState:
    """Return a minimal ChatState, with optional field overrides."""
    state: ChatState = {
        "messages": [],
        "conversation_id": "test-conv-id",
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "accounts": None,
        "aggregations": {"spending_by_category": {"Food": 200.0}},
        "habits": [
            {"category": "Food", "observation": "High spend", "severity": "warning"}
        ],
        "suggestions": [
            {
                "title": "Cut dining",
                "body": "Consider cooking at home",
                "severity": "info",
            }
        ],
        "goals_prompt": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_tool_call_message() -> AIMessage:
    """Return an AIMessage that contains a tool call (ReAct step)."""
    msg = AIMessage(content="")
    msg.tool_calls = [{"name": "get_spending_by_category", "args": {}, "id": "call-1"}]
    return msg


def _make_plain_message(content: str = "Here is your answer.") -> AIMessage:
    """Return an AIMessage with no tool calls (final answer)."""
    msg = AIMessage(content=content)
    msg.tool_calls = []
    return msg


# ── ReAct routing ─────────────────────────────────────────────────────────────


class TestShouldContinue:
    """_should_continue routes to 'tools' or END based on the last message."""

    def test_routes_to_tools_when_tool_calls_present(self) -> None:
        state = _base_state(messages=[_make_tool_call_message()])
        assert _should_continue(state) == "tools"

    def test_routes_to_end_when_no_tool_calls(self) -> None:
        state = _base_state(messages=[_make_plain_message()])
        from langgraph.graph import END

        assert _should_continue(state) == END

    def test_routes_to_end_on_plain_text_response(self) -> None:
        from langgraph.graph import END

        state = _base_state(
            messages=[_make_plain_message("Spending was $500 this month.")]
        )
        assert _should_continue(state) == END


# ── System prompt construction ────────────────────────────────────────────────


class TestSystemPrompt:
    """make_chat_node builds a SystemMessage injected before user messages."""

    def _capture_system_message(self, state: ChatState) -> SystemMessage:
        """Run the chat node with a mock LLM and return the SystemMessage it was called with."""
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = _make_plain_message()

        with patch("agents.chat.nodes.ChatOpenAI", return_value=mock_llm):
            node = make_chat_node(DEFAULT_CONFIG, tools=[])
            node(state)

        call_args = mock_llm.invoke.call_args[0][0]  # list of messages passed
        system_msgs = [m for m in call_args if isinstance(m, SystemMessage)]
        assert system_msgs, "No SystemMessage was injected"
        return system_msgs[0]

    def test_goals_injected_when_present(self) -> None:
        goals = (
            "The user has defined the following financial goals.\n- Save $1000/month"
        )
        state = _base_state(
            messages=[HumanMessage(content="How am I doing?")],
            goals_prompt=goals,
        )
        prompt = self._capture_system_message(state)
        assert goals in prompt.content

    def test_goals_omitted_when_none(self) -> None:
        state = _base_state(
            messages=[HumanMessage(content="How am I doing?")],
            goals_prompt=None,
        )
        prompt = self._capture_system_message(state)
        assert "financial goals" not in prompt.content.lower()

    def test_aggregations_context_included(self) -> None:
        agg = {"spending_by_category": {"Groceries": 350.0, "Transport": 120.0}}
        state = _base_state(
            messages=[HumanMessage(content="What did I spend on groceries?")],
            aggregations=agg,
        )
        prompt = self._capture_system_message(state)
        assert "Groceries" in prompt.content
        assert "350" in prompt.content

    def test_date_range_included_in_prompt(self) -> None:
        state = _base_state(
            messages=[HumanMessage(content="Summary please")],
            date_from="2025-06-01",
            date_to="2025-06-30",
        )
        prompt = self._capture_system_message(state)
        assert "2025-06-01" in prompt.content
        assert "2025-06-30" in prompt.content

    def test_habits_and_suggestions_included(self) -> None:
        habits = [
            {
                "category": "Dining",
                "observation": "Very frequent",
                "severity": "warning",
            }
        ]
        suggestions = [
            {"title": "Budget", "body": "Set a monthly limit", "severity": "info"}
        ]
        state = _base_state(
            messages=[HumanMessage(content="Any tips?")],
            habits=habits,
            suggestions=suggestions,
        )
        prompt = self._capture_system_message(state)
        assert "Dining" in prompt.content
        assert "Budget" in prompt.content


# ── Graph topology ────────────────────────────────────────────────────────────


class TestGraphTopology:
    """Verify the compiled graph has the expected nodes and edges."""

    def test_graph_has_required_nodes(self) -> None:
        g = make_graph(checkpointer=None)
        assert "load_context" in g.nodes
        assert "chat" in g.nodes
        assert "tools" in g.nodes

    def test_single_tool_call_then_answer(self) -> None:
        """Graph completes: load_context → chat (tool call) → tools → chat (answer) → END."""
        tool_call_msg = _make_tool_call_message()
        plain_msg = _make_plain_message("Your food spend is $200.")

        # LLM returns a tool call on first invocation, then a plain answer
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.side_effect = [tool_call_msg, plain_msg]

        mock_db = MagicMock()
        mock_db.get_transaction_date_range.return_value = ("2026-01-01", "2026-01-31")
        mock_db.get_insights_cache.return_value = {
            "aggregations": {
                "spending_by_category": {"Food": 200.0},
                "transfer_fees_summary": {},
            },
            "habits": [],
            "suggestions": [],
        }
        mock_db.get_active_goals.return_value = []

        mock_tool = MagicMock()
        mock_tool.name = "get_spending_by_category"
        mock_tool.invoke.return_value = [{"category": "Food", "total": 200.0}]

        with (
            patch("agents.chat.nodes.ChatOpenAI", return_value=mock_llm),
            patch("agents.chat.nodes.DatabaseService", return_value=mock_db),
        ):
            g = make_graph(checkpointer=None)
            result = g.invoke(
                {
                    "messages": [HumanMessage(content="How much did I spend on food?")],
                    "conversation_id": "test-conv",
                },
            )

        last = result["messages"][-1]
        assert isinstance(last, AIMessage)
        assert last.content == "Your food spend is $200."
        # LLM was called twice: once producing tool call, once producing final answer
        assert mock_llm.invoke.call_count == 2

    def test_direct_answer_skips_tools(self) -> None:
        """Graph completes in one LLM call when no tool is needed."""
        plain_msg = _make_plain_message("You spent $500 total.")

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = plain_msg

        mock_db = MagicMock()
        mock_db.get_transaction_date_range.return_value = ("2026-01-01", "2026-01-31")
        mock_db.get_insights_cache.return_value = {
            "aggregations": {"spending_by_category": {}, "transfer_fees_summary": {}},
            "habits": [],
            "suggestions": [],
        }
        mock_db.get_active_goals.return_value = []

        with (
            patch("agents.chat.nodes.ChatOpenAI", return_value=mock_llm),
            patch("agents.chat.nodes.DatabaseService", return_value=mock_db),
        ):
            g = make_graph(checkpointer=None)
            result = g.invoke(
                {
                    "messages": [HumanMessage(content="Quick summary?")],
                    "conversation_id": "test-conv",
                },
            )

        assert mock_llm.invoke.call_count == 1
        assert result["messages"][-1].content == "You spent $500 total."
