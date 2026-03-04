from pydantic import BaseModel
from typing import TypedDict, Annotated

class Transaction(BaseModel):
    id: str # UUID generated at normalization
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
    source_file: str
    file_type: str
    content: str # raw text for pdf, markdown table for xlsx

# Reducer function to keep the last value of the list
def keep_last(old: list, new: list) -> list:
    return new if new else old

class ReconciliationState(TypedDict):
    source_folder: list[str]
    raw_documents: list[RawDocument]
    transactions: Annotated[list[Transaction], keep_last]
    duplicates: list[Transaction]
    suspicious: list[Transaction]
    exchange_rates: dict # format: {"YYYY-MM-DD_CURRENCY": float}
    report: str | None

def initial_state(source_folder: str) -> ReconciliationState:
    return {
        "source_folder": source_folder,
        "raw_documents": [],
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None
    }