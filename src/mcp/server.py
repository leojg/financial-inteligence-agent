"""MCP server — FastMCP tools over the reconciliation, insights, and chat services."""

import base64
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypedDict

from mcp.server.fastmcp import FastMCP

from services import chat as chat_service
from services import insights, reconciliation
from shared.db import run_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Run DB migrations on startup."""
    logger.info("Running database migrations...")
    run_migrations()
    logger.info("MCP server ready.")
    yield


mcp = FastMCP(
    name="finance-intelligence-agent",
    stateless_http=True,
    json_response=True,
    lifespan=lifespan,
    host="0.0.0.0",
    port=8811,
)


class FileInput(TypedDict):
    """Single uploaded file for reconciliation (base64-encoded body)."""

    filename: str
    content_base64: str


@mcp.tool()
def start_reconciliation(
    files: list[FileInput],
    auto_approve: bool = True,
) -> dict[str, Any]:
    """Start reconciliation on uploaded bank statements/receipts.

    Returns run_id and thread_id; use both with get_reconciliation_status.
    """
    decoded = [(f["filename"], base64.b64decode(f["content_base64"])) for f in files]
    result = reconciliation.run(files=decoded, auto_approve=auto_approve)
    return result.model_dump()


@mcp.tool()
def get_reconciliation_status(run_id: str, thread_id: str) -> dict[str, Any]:
    """Check the status of a reconciliation run (same thread_id from start_reconciliation)."""
    return reconciliation.get_status(run_id, thread_id).model_dump()


@mcp.tool()
def run_insights(
    date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    """Run insights on the given date range."""
    return insights.run(date_from=date_from, date_to=date_to).model_dump()


@mcp.tool()
def get_latest_insights() -> dict[str, Any]:
    """Get the latest insights."""
    result = insights.get_latest()
    if result is None:
        return {"error": "No cached insights found. Run the pipeline first."}
    return result.model_dump()


@mcp.tool()
def chat(message: str, conversation_id: str | None = None) -> dict[str, Any]:
    """Send a message to the chat agent."""
    return chat_service.send_message(message, conversation_id).model_dump()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
