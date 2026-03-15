"""Shared data models used across agents."""

from typing import Literal

from pydantic import BaseModel


class Transaction(BaseModel):
    """A single normalized transaction (from normalize/convert_currency)."""

    id: str  # UUID generated at normalization
    date: str # ISO Format
    amount_original: float
    amount_base: float | None = None
    currency: str
    merchant: str
    account: str
    source_file: str
    category: str | None = None
    duplicate_of: str | None = None
    suspicious: bool = False
    suspicious_reason: str | None = None
    needs_review: bool = False
    review_reason: str | None = None
    review_status: str | None = None
    confidence: float | None = None # Confidence score for the transaction


class RawDocument(BaseModel):
    """Raw document content from ingest (source_file, file_type, content)."""

    source_file: str
    file_type: str
    content: str  # raw text for pdf, markdown table for xlsx
    confidence: float | None = None # Confidence score for the image document

class Habit(BaseModel):
    """A habit detected in the spending data."""

    category: str
    observation: str
    severity: Literal["info", "warning", "critical"]


class Suggestion(BaseModel):
    """A suggestion for the user to improve their spending habits."""
    
    type: str
    title: str
    body: str
    severity: Literal["info", "warning", "critical"]


class InsightsOutput(BaseModel):
    """The output of the insights agent."""
    
    habits: list[Habit]
    suggestions: list[Suggestion]

class ReceiptLine(BaseModel):
    """A single line item within a receipt."""

    description: str
    amount: float
    quantity: float | None = None

class Receipt(BaseModel):
    """A parsed receipt document mapped to a single Transaction."""

    date: str | None
    merchant: str
    account: str = "Receipt"           # payment method or "Receipt"
    source_file: str
    currency: str | None
    total: float           # the single amount that maps to Transaction
    lines: list[ReceiptLine]
    confidence: float | None = None