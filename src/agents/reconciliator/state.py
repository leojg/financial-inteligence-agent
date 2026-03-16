"""State types for the reconciliation graph (Transaction, RawDocument, ReconciliationState)."""

import uuid
from typing import Annotated, Any, TypedDict

from shared.models import RawDocument, Receipt, Transaction


def keep_last(old: list[Any], new: list[Any]) -> list[Any]:
    """Reducer that keeps the last value of the list (for state updates)."""
    return new if new else old

def merge_raw_documents(old: list[Any], new: list[Any]) -> list[Any]:
    """Append new raw_documents to existing (for parallel ingest + ingest_images)."""
    return (old or []) + (new or [])

def merge_receipts(old: list[Any], new: list[Any]) -> list[Any]:
    """Append new receipts to existing (parallel ingest paths)."""
    return (old or []) + (new or [])

class ReconciliationState(TypedDict):
    """Graph state: source_paths, source_files, raw_documents, transactions, duplicates, suspicious, report."""

    source_paths: list[str]
    source_files: list[str]
    raw_documents: Annotated[list[RawDocument], merge_raw_documents]
    transactions: Annotated[list[Transaction], keep_last]
    receipts: Annotated[list[Receipt], merge_receipts]
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
        "receipts": [],
    }


__all__ = ["RawDocument", "Receipt", "ReconciliationState", "Transaction", "initial_state", "keep_last", "merge_raw_documents", "merge_receipts"]