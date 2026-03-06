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
            source_file TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            transactions_json TEXT NOT NULL,
            PRIMARY KEY (source_file, content_hash)
        );
    """)
    conn.commit()


def get_checkpointer() -> SqliteSaver:
    """Return a SqliteSaver using the shared DB so Studio and Streamlit share checkpoint state."""
    return SqliteSaver(get_connection())
