"""State types for the reconciliation graph (Transaction, RawDocument, ReconciliationState)."""

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


class RawDocument(BaseModel):
    """Raw document content from ingest (source_file, file_type, content)."""

    source_file: str
    file_type: str
    content: str  # raw text for pdf, markdown table for xlsx


def keep_last(old: list[Any], new: list[Any]) -> list[Any]:
    """Reducer that keeps the last value of the list (for state updates)."""
    return new if new else old


class ReconciliationState(TypedDict):
    """Graph state: source_folder, raw_documents, transactions, duplicates, suspicious, report."""

    source_folder: str
    raw_documents: list[RawDocument]
    transactions: Annotated[list[Transaction], keep_last]
    duplicates: list[Transaction]
    suspicious: list[Transaction]
    exchange_rates: dict[str, float]
    report: str | None


def initial_state(source_folder: str) -> ReconciliationState:
    """Return initial graph state for the given source_folder."""
    return {
        "source_folder": source_folder,
        "raw_documents": [],
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None
    }