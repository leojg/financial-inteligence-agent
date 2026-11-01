"""Shared data models used across agents."""

from typing import Any, Literal

from pydantic import BaseModel

SignSource = Literal["column", "marker", "balance", "llm"]
BalanceStatus = Literal["ok", "mismatch", "no_balance"]
AccountType = Literal["deposit", "card"]


class Transaction(BaseModel):
    """A single normalized transaction (from normalize/convert_currency)."""

    id: str  # UUID generated at normalization
    date: str  # ISO Format
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
    confidence: float | None = None  # Confidence score for the transaction
    # Traceability to the printed statement (v1.6); None for receipts and legacy rows.
    statement_id: str | None = None
    page: int | None = None
    row_index: int | None = None
    raw_text: str | None = None
    amount_raw: str | None = None
    balance_after: float | None = None
    sign_source: SignSource | None = None
    installment_n: int | None = None
    installment_m: int | None = None


class StatementRow(BaseModel):
    """One printed movement of a statement: the cells as printed beside their parsed values."""

    row_index: int  # order within the statement, 0-based
    page: int | None = None  # 1-based page number
    raw_text: str | None = None  # the printed line(s) the row came from
    date_raw: str | None = None
    description_raw: str = ""
    debit_raw: str | None = None
    credit_raw: str | None = None
    amount_raw: str | None = None
    balance_raw: str | None = None
    # Parsed values; None when the printed value could not be read (never zero).
    date: str | None = None
    amount: float | None = None  # signed: negative = money out / charge
    amount_alternatives: list[float] = []  # other magnitudes an ambiguous amount allows
    balance_after: float | None = None
    sign_source: SignSource | None = None
    installment_n: int | None = None
    installment_m: int | None = None


class Statement(BaseModel):
    """What a statement says about itself — header, balances, rows — and whether its arithmetic closes.

    A card statement with two currencies becomes two Statements (one per currency)
    sharing the same ``file_sha256``.
    """

    id: str
    source_file: str
    file_sha256: str
    institution: str | None = None
    account: str | None = None
    account_type: AccountType | None = None
    currency: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    opening_balance: float | None = None
    opening_balance_raw: str | None = None
    closing_balance: float | None = None
    closing_balance_raw: str | None = None
    printed_totals: dict[str, float] = {}  # "debits" / "credits" as printed
    rows: list[StatementRow] = []
    extractor: str | None = None  # "legacy", "table", "llm"
    extractor_version: str | None = None
    balance_status: BalanceStatus | None = None
    balance_detail: str | None = None
    breaks: list[
        dict[str, Any]
    ] = []  # running-balance breaks, see agents.reconciliator.statements.balance
    diagnostics: list[
        dict[str, Any]
    ] = []  # {"reason", "detail", "page", "count", "amount"}


class RawDocument(BaseModel):
    """Raw document content from ingest (source_file, file_type, content)."""

    source_file: str
    file_type: str
    content: str  # raw text for pdf, markdown table for xlsx
    confidence: float | None = None  # Confidence score for the image document


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

    id: str | None = None
    receipt_id: str | None = None
    line_number: int | None = None
    description: str
    quantity: float = 1.0
    unit_price: float | None = None
    amount: float
    category: str | None = None


class Receipt(BaseModel):
    """A parsed receipt document mapped to a single Transaction."""

    id: str | None = None
    run_id: str | None = None
    transaction_id: str | None = None
    source_file: str
    merchant: str
    merchant_normalized: str | None = None
    date: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    tax_rate: float | None = None
    total: float
    receipt_number: str | None = None
    confidence: float | None = None
    raw_content: str | None = None
    lines: list[ReceiptLine] = []
