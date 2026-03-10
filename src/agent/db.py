"""Shared SQLite persistence for the finance agent.

DB path, checkpointer for LangGraph, and app tables (exchange_rates,
normalized_document_cache). Both LangGraph Studio and Streamlit use the same DB
via get_checkpointer().
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_CHECKPOINT_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=(
        tuple(["agent.state", "RawDocument"]),
        tuple(["agent.state", "Transaction"]),
    ),
)

_DEFAULT_DB_PATH = "data/agent.db"
_connection: sqlite3.Connection | None = None


def get_db_path() -> str:
    """Return DB path from env FINANCE_AGENT_DB_PATH, or default data/agent.db."""
    path = os.getenv("FINANCE_AGENT_DB_PATH", _DEFAULT_DB_PATH)
    path = path.strip()
    if path:
        parent = Path(path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
    return path or _DEFAULT_DB_PATH


def get_connection() -> sqlite3.Connection:
    """Return a shared SQLite connection to the app DB; ensures schema on first use."""
    global _connection
    if _connection is None:
        path = get_db_path()
        _connection = sqlite3.connect(path, check_same_thread=False)
        ensure_schema(_connection)
    return _connection


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create app tables if they do not exist (exchange_rates, normalized_document_cache)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            date TEXT NOT NULL,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            rate REAL NOT NULL,
            PRIMARY KEY (date, from_currency, to_currency)
        );
        CREATE TABLE IF NOT EXISTS normalized_document_cache (
            content_hash TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            transactions_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs_history (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            source_paths TEXT NOT NULL,
            status TEXT NOT NULL,
            total_transactions INTEGER DEFAULT 0,
            total_duplicates INTEGER DEFAULT 0,
            total_suspicious INTEGER DEFAULT 0,
            base_currency TEXT NOT NULL DEFAULT 'USD'
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            date TEXT NOT NULL,
            amount_original REAL NOT NULL,
            amount_base REAL,
            currency TEXT NOT NULL,
            merchant TEXT NOT NULL,
            merchant_normalized TEXT NOT NULL,
            account TEXT NOT NULL,
            source_file TEXT NOT NULL,
            category TEXT,
            duplicate_of TEXT,
            suspicious INTEGER NOT NULL DEFAULT 0,
            suspicious_reason TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT,
            review_status TEXT,
            confidence REAL,
            FOREIGN KEY (run_id) REFERENCES runs_history(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_transactions_run_id
            ON transactions(run_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_fingerprint
            ON transactions(fingerprint);

        CREATE TABLE IF NOT EXISTS merchant_categories (
            merchant_normalized TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'llm',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS duplicate_pairs (
            fingerprint_a TEXT NOT NULL,
            fingerprint_b TEXT NOT NULL,
            is_duplicate INTEGER NOT NULL,
            reason TEXT,
            PRIMARY KEY (fingerprint_a, fingerprint_b)
        );
    """)
    conn.commit()


def get_checkpointer() -> SqliteSaver:
    """Return a SqliteSaver using the shared DB so Studio and Streamlit share checkpoint state."""
    return SqliteSaver(get_connection(), serde=_CHECKPOINT_SERDE)