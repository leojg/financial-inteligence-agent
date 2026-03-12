"""Shared SQLite persistence for the finance agent.

DB path, checkpointer for LangGraph, and app tables (exchange_rates,
normalized_document_cache). Both LangGraph Studio and Streamlit use the same DB
via get_checkpointer().
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_CHECKPOINT_SERDE = JsonPlusSerializer(
    allowed_json_modules=(
        ("shared.models", "RawDocument"),
        ("shared.models", "Transaction"),
    ),
)

_DEFAULT_DB_PATH = "data/agent.db"
_connection: sqlite3.Connection | None = None
_checkpoint_connection: sqlite3.Connection | None = None


def get_db_path() -> str:
    """Return DB path from env FINANCE_AGENT_DB_PATH, or default data/agent.db."""
    path = os.getenv("FINANCE_AGENT_DB_PATH", _DEFAULT_DB_PATH)
    path = path.strip()
    if path:
        parent = Path(path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
    return path or _DEFAULT_DB_PATH


def _connect(path: str, *, for_checkpointer: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode for better concurrent access."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    if not for_checkpointer:
        ensure_schema(conn)
    return conn


def get_connection() -> sqlite3.Connection:
    """Return a shared SQLite connection to the app DB; ensures schema on first use."""
    global _connection
    if _connection is None:
        _connection = _connect(get_db_path())
    return _connection


def _get_checkpoint_connection() -> sqlite3.Connection:
    """Dedicated connection for the LangGraph checkpointer to avoid concurrent commit on the same connection when the graph runs fan-out (multiple threads)."""
    global _checkpoint_connection
    if _checkpoint_connection is None:
        _checkpoint_connection = _connect(get_db_path(), for_checkpointer=True)
    return _checkpoint_connection


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

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );
    """)
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Run pending migrations in order. Each migration N runs when current version < N."""
    cur = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    current = row[0] if row else 0

    if current < 1:
        # Migration 1: bootstrap (schema already created by ensure_schema)
        conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (1)")
        conn.commit()

    if current < 2:
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_transactions_date
                ON transactions(date);
            CREATE INDEX IF NOT EXISTS idx_transactions_account
                ON transactions(account);
            CREATE INDEX IF NOT EXISTS idx_transactions_category
                ON transactions(category);
            CREATE INDEX IF NOT EXISTS idx_transactions_run_date
                ON transactions(run_id, date);
        """)
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.commit()


def get_checkpointer() -> SqliteSaver:
    """Return a SqliteSaver using a dedicated checkpoint connection (same DB file, WAL mode).

    Avoids SystemError when fan-out runs multiple threads writing checkpoints.
    """
    return SqliteSaver(_get_checkpoint_connection(), serde=_CHECKPOINT_SERDE)
