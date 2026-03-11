"""State types for the reconciliation graph (Transaction, RawDocument, ReconciliationState)."""

import uuid
from typing import Annotated, Any, TypedDict

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


def keep_last(old: list[Any], new: list[Any]) -> list[Any]:
    """Reducer that keeps the last value of the list (for state updates)."""
    return new if new else old

def merge_raw_documents(old: list[Any], new: list[Any]) -> list[Any]:
    """Append new raw_documents to existing (for parallel ingest + ingest_images)."""
    return (old or []) + (new or [])

class ReconciliationState(TypedDict):
    """Graph state: source_paths, source_files, raw_documents, transactions, duplicates, suspicious, report."""

    source_paths: list[str]
    source_files: list[str]
    raw_documents: Annotated[list[RawDocument], merge_raw_documents]
    transactions: Annotated[list[Transaction], keep_last]
    duplicates: list[Transaction]
    suspicious: list[Transaction]
    exchange_rates: dict[str, float]
    report: str | None
    low_confidence_decisions: list[dict[str, Any]] | None
    run_id: str


def initial_state(source_paths: list[str]) -> ReconciliationState:
    """Return initial graph state for the given source_folder."""
    return {
        "source_paths": source_paths,
        "source_files": [],
        "raw_documents": [],
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
        "low_confidence_decisions": None,
        "run_id": str(uuid.uuid4()),
    }