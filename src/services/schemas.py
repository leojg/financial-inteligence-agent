"""Pydantic schemas shared across the services layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class RunStatus(str, Enum):
    """Status of a reconciliation run."""

    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


class RunResult(BaseModel):
    """Result returned by the reconciliation service."""

    run_id: str
    status: RunStatus
    thread_id: str
    total_transactions: int | None = None
    total_duplicates: int | None = None
    total_suspicious: int | None = None
    flagged_transactions: list[dict[str, Any]] | None = None
    report: str | None = None
    interrupt_at: str | None = None


class InsightsResponse(BaseModel):
    """Response from the insights pipeline."""

    date_from: str
    date_to: str
    aggregations: dict[str, Any]
    habits: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]


class ChatResponse(BaseModel):
    """Response from the chat agent."""

    conversation_id: str
    response: str
