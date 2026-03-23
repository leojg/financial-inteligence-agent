"""SQLAlchemy ORM models — schema source of truth for Alembic and app queries."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


class ExchangeRate(Base):
    """Cached FX rate for a given date and currency pair."""

    __tablename__ = "exchange_rates"

    date = Column(String, primary_key=True)
    from_currency = Column(String, primary_key=True)
    to_currency = Column(String, primary_key=True)
    rate = Column(Float, nullable=False)


class NormalizedDocumentCache(Base):
    """Cached normalized transactions JSON keyed by source file content hash."""

    __tablename__ = "normalized_document_cache"

    content_hash = Column(String, primary_key=True)
    source_file = Column(String, nullable=False)
    transactions_json = Column(Text, nullable=False)


class RunHistory(Base):
    """A single reconciliation run (ingest → normalize → categorize pipeline)."""

    __tablename__ = "runs_history"

    run_id = Column(String, primary_key=True)
    thread_id = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    completed_at = Column(String)
    source_paths = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    total_transactions = Column(Integer, default=0)
    total_duplicates = Column(Integer, default=0)
    total_suspicious = Column(Integer, default=0)
    base_currency = Column(String, nullable=False, default="USD")

    transactions = relationship("TransactionRecord", back_populates="run")
    receipts = relationship("ReceiptRecord", back_populates="run")


class TransactionRecord(Base):
    """A single normalized and categorized financial transaction."""

    __tablename__ = "transactions"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("runs_history.run_id"), nullable=False)
    fingerprint = Column(String, nullable=False)
    date = Column(String, nullable=False)
    amount_original = Column(Float, nullable=False)
    amount_base = Column(Float)
    currency = Column(String, nullable=False)
    merchant = Column(String, nullable=False)
    merchant_normalized = Column(String, nullable=False)
    account = Column(String, nullable=False)
    source_file = Column(String, nullable=False)
    category = Column(String)
    duplicate_of = Column(String)
    suspicious = Column(Integer, nullable=False, default=0)
    suspicious_reason = Column(String)
    needs_review = Column(Integer, nullable=False, default=0)
    review_reason = Column(String)
    review_status = Column(String)
    confidence = Column(Float)

    run = relationship("RunHistory", back_populates="transactions")

    __table_args__ = (
        Index("idx_transactions_run_id", "run_id"),
        Index("idx_transactions_fingerprint", "fingerprint"),
        Index("idx_transactions_date", "date"),
        Index("idx_transactions_account", "account"),
        Index("idx_transactions_category", "category"),
        Index("idx_transactions_run_date", "run_id", "date"),
    )


class MerchantCategory(Base):
    """LLM-assigned or manually set category for a normalized merchant name."""

    __tablename__ = "merchant_categories"

    merchant_normalized = Column(String, primary_key=True)
    category = Column(String, nullable=False)
    source = Column(String, nullable=False, default="llm")
    updated_at = Column(String, nullable=False)


class DuplicatePair(Base):
    """Cached result of a duplicate-pair check between two transaction fingerprints."""

    __tablename__ = "duplicate_pairs"

    fingerprint_a = Column(String, primary_key=True)
    fingerprint_b = Column(String, primary_key=True)
    is_duplicate = Column(Integer, nullable=False)
    reason = Column(String)


class UserGoal(Base):
    """A user-defined financial goal used to contextualize insights."""

    __tablename__ = "user_goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class InsightsCache(Base):
    """Cached insights output keyed by date range and account set."""

    __tablename__ = "insights_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_from = Column(String, nullable=False)
    date_to = Column(String, nullable=False)
    accounts_hash = Column(String, nullable=False)
    aggregations_json = Column(Text, nullable=False)
    habits_json = Column(Text, nullable=False)
    suggestions_json = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        Index(
            "uq_insights_cache", "date_from", "date_to", "accounts_hash", unique=True
        ),
    )


class ReceiptRecord(Base):
    """A parsed receipt linked to a transaction."""

    __tablename__ = "receipts"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("runs_history.run_id"), nullable=False)
    transaction_id = Column(String, ForeignKey("transactions.id"))
    source_file = Column(String, nullable=False)
    merchant = Column(String, nullable=False)
    merchant_normalized = Column(String, nullable=False)
    date = Column(String)
    currency = Column(String)
    subtotal = Column(Float)
    tax_amount = Column(Float)
    tax_rate = Column(Float)
    total = Column(Float, nullable=False)
    receipt_number = Column(String)
    confidence = Column(Float)
    raw_content = Column(Text)
    created_at = Column(String, nullable=False)

    run = relationship("RunHistory", back_populates="receipts")
    lines = relationship("ReceiptLineRecord", back_populates="receipt")

    __table_args__ = (
        Index("idx_receipts_run_id", "run_id"),
        Index("idx_receipts_transaction_id", "transaction_id"),
    )


class ReceiptLineRecord(Base):
    """A single line item within a receipt."""

    __tablename__ = "receipt_lines"

    id = Column(String, primary_key=True)
    receipt_id = Column(String, ForeignKey("receipts.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    description = Column(String, nullable=False)
    quantity = Column(Float, default=1)
    unit_price = Column(Float)
    amount = Column(Float, nullable=False)
    category = Column(String)

    receipt = relationship("ReceiptRecord", back_populates="lines")

    __table_args__ = (Index("idx_receipt_lines_receipt_id", "receipt_id"),)
