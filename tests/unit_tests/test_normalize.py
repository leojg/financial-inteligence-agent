"""Unit tests for the normalize node."""

import hashlib
import json
import sqlite3
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from agent.configuration import DEFAULT_CONFIG
from agent.db import ensure_schema
from agent.nodes import make_normalize_node
from agent.state import RawDocument


def _base_state(raw_documents):
    """Minimal state shape for normalize (reads raw_documents)."""
    return {
        "source_folder": "data",
        "raw_documents": raw_documents,
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }


@pytest.fixture
def config():
    return DEFAULT_CONFIG


@pytest.fixture
def in_memory_db():
    """Fresh in-memory SQLite with app schema (no cached rows)."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def raw_doc_single():
    """One raw document (e.g. from ingest)."""
    content = "| Date | Description | Amount |\n| 2026-01-01 | SUPER | -100 |"
    return RawDocument(
        source_file="data/account.xlsx",
        file_type="xlsx",
        content=content,
    )


@pytest.fixture
def state_one_raw_doc(raw_doc_single):
    """State with one raw document to normalize."""
    return _base_state([raw_doc_single])


def test_normalize_empty_raw_documents_returns_empty_transactions(config):
    """No raw_documents yields empty transactions list."""
    state = _base_state([])
    with patch("agent.nodes.get_connection") as mock_get_conn:
        mock_get_conn.return_value = sqlite3.connect(":memory:")
        ensure_schema(mock_get_conn.return_value)
        normalize = make_normalize_node(config)
        result = normalize(state)
    assert result["transactions"] == []


def test_normalize_cache_hit_returns_cached_transactions_no_llm(
    config, in_memory_db, raw_doc_single, state_one_raw_doc
):
    """When cache has a hit for (source_file, content_hash), transactions come from cache and LLM is not called."""
    content_hash = hashlib.sha256(raw_doc_single.content.encode()).hexdigest()
    cached_txs = [
        {
            "id": "cached-001",
            "date": "2026-01-01",
            "amount_original": -100.0,
            "amount_base": None,
            "currency": "USD",
            "merchant": "SUPER",
            "account": "Checking",
            "source_file": raw_doc_single.source_file,
        }
    ]
    in_memory_db.execute(
        "INSERT INTO normalized_document_cache (source_file, content_hash, transactions_json) VALUES (?, ?, ?)",
        (raw_doc_single.source_file, content_hash, json.dumps(cached_txs)),
    )
    in_memory_db.commit()

    with patch("agent.nodes.get_connection", return_value=in_memory_db):
        with patch("agent.nodes.ChatOpenAI") as MockLLM:
            normalize = make_normalize_node(config)
            result = normalize(state_one_raw_doc)
            MockLLM.return_value.invoke.assert_not_called()

    assert len(result["transactions"]) == 1
    t = result["transactions"][0]
    assert t.id == "cached-001"
    assert t.merchant == "SUPER"
    assert t.amount_original == -100.0


def test_normalize_cache_miss_calls_llm_and_writes_cache(
    config, in_memory_db, state_one_raw_doc
):
    """Cache miss: LLM returns valid JSON; transactions get ids and cache is written."""
    llm_response = [
        {
            "date": "2026-01-01",
            "amount_original": -100.0,
            "amount_base": None,
            "currency": "USD",
            "merchant": "SUPER",
            "account": "Checking",
            "source_file": "data/account.xlsx",
        }
    ]
    with patch("agent.nodes.get_connection", return_value=in_memory_db):
        with patch("agent.nodes.ChatOpenAI") as MockLLM:
            with patch("agent.nodes.uuid.uuid4", return_value=UUID("00000000-0000-0000-0000-000000000001")):
                mock_llm_instance = MockLLM.return_value
                mock_llm_instance.invoke.return_value = MagicMock(
                    content=json.dumps(llm_response)
                )
                normalize = make_normalize_node(config)
                result = normalize(state_one_raw_doc)

    assert len(result["transactions"]) == 1
    t = result["transactions"][0]
    assert t.merchant == "SUPER"
    assert t.amount_original == -100.0
    assert t.id is not None

    cur = in_memory_db.execute(
        "SELECT source_file, content_hash, transactions_json FROM normalized_document_cache"
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None
    assert row[0] == "data/account.xlsx"
    stored = json.loads(row[2])
    assert len(stored) == 1
    assert stored[0]["merchant"] == "SUPER"


def test_normalize_invalid_json_skips_doc_continues(config, in_memory_db, state_one_raw_doc):
    """LLM returns invalid JSON; that doc is skipped, no exception, transactions empty or partial."""
    with patch("agent.nodes.get_connection", return_value=in_memory_db):
        with patch("agent.nodes.ChatOpenAI") as MockLLM:
            mock_llm_instance = MockLLM.return_value
            mock_llm_instance.invoke.return_value = MagicMock(content="not valid json")
            normalize = make_normalize_node(config)
            result = normalize(state_one_raw_doc)

    assert result["transactions"] == []
    # Cache should not have been written for this doc
    cur = in_memory_db.execute("SELECT COUNT(*) FROM normalized_document_cache")
    assert cur.fetchone()[0] == 0
    cur.close()


def test_normalize_accepts_dict_raw_documents(config, in_memory_db):
    """raw_documents may be dicts (e.g. from checkpoint); node converts to RawDocument."""
    state = _base_state(
        [
            {
                "source_file": "data/other.xlsx",
                "file_type": "xlsx",
                "content": "| 2026-01-02 | COFFEE | -5 |",
            }
        ]
    )
    content_hash = hashlib.sha256(state["raw_documents"][0]["content"].encode()).hexdigest()
    cached = [{"id": "from-dict", "date": "2026-01-02", "amount_original": -5.0, "amount_base": None, "currency": "USD", "merchant": "COFFEE", "account": "Savings", "source_file": "data/other.xlsx"}]
    in_memory_db.execute(
        "INSERT INTO normalized_document_cache (source_file, content_hash, transactions_json) VALUES (?, ?, ?)",
        ("data/other.xlsx", content_hash, json.dumps(cached)),
    )
    in_memory_db.commit()

    with patch("agent.nodes.get_connection", return_value=in_memory_db):
        normalize = make_normalize_node(config)
        result = normalize(state)

    assert len(result["transactions"]) == 1
    assert result["transactions"][0].id == "from-dict"
    assert result["transactions"][0].merchant == "COFFEE"
