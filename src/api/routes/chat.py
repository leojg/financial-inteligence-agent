"""Chat API routes."""

from __future__ import annotations

from fastapi import APIRouter

from services.chat import send_message
from services.schemas import ChatResponse

router = APIRouter(tags=["chat"])

@router.post("/", response_model=ChatResponse)
async def chat(message: str, conversation_id: str | None = None) -> ChatResponse:
    """Send a message to the chat agent and return the response."""
    return send_message(message=message, conversation_id=conversation_id)
