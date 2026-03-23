"""Unit tests for the normalize node."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import text

import shared.db as _db
from agents.reconciliator.configuration import DEFAULT_CONFIG
from agents.reconciliator.nodes import make_normalize_node
from shared.models import RawDocument

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def in_memory_session():
    """Return a session backed by the already-patched in-memory engine (from conftest autouse)."""
    with _db.get_session() as session:
        yield session


@pytest.fixture()
def config():
    """Default reconciliator config."""
    return DEFAULT_CONFIG


@pytest.fixture()
def raw_doc_single() -> RawDocument:
    """One raw document (e.g. from ingest)."""
    return RawDocument(
        source_file="data/account.xlsx",
        file_type="xlsx",
        content="| Date | Description | Amount |\n| 2026-01-01 | SUPER | -100 |",
    )


def _base_state(raw_documents: list[Any]) -> dict[str, Any]:
    """Minimal state shape for normalize."""
    return {
        "source_folder": "data",
        "raw_documents": raw_documents,
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_normalize_empty_raw_documents_returns_empty_transactions(
    config, in_memory_session
) -> None:
    """No raw_documents yields empty transactions list."""
    state = _base_state([])
    normalize = make_normalize_node(config)
    result = normalize(state)
    assert result["transactions"] == []


def test_normalize_cache_hit_returns_cached_transactions_no_llm(
    config, in_memory_session, raw_doc_single
) -> None:
    """Cache hit: transactions come from cache and LLM is not called."""
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
    in_memory_session.execute(
        text(
            "INSERT INTO normalized_document_cache (source_file, content_hash, transactions_json)"
            " VALUES (:sf, :ch, :tj)"
        ),
        {
            "sf": raw_doc_single.source_file,
            "ch": content_hash,
            "tj": json.dumps(cached_txs),
        },
    )
    in_memory_session.commit()

    with patch("agents.reconciliator.nodes.ChatOpenAI") as MockLLM:
        normalize = make_normalize_node(config)
        result = normalize(_base_state([raw_doc_single]))
        MockLLM.return_value.invoke.assert_not_called()

    assert len(result["transactions"]) == 1
    t = result["transactions"][0]
    assert t.id == "cached-001"
    assert t.merchant == "SUPER"
    assert t.amount_original == -100.0


def test_normalize_cache_miss_calls_llm_and_writes_cache(
    config, in_memory_session, raw_doc_single
) -> None:
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
    with patch("agents.reconciliator.nodes.ChatOpenAI") as MockLLM:
        with patch(
            "agents.reconciliator.nodes.uuid.uuid4",
            return_value=UUID("00000000-0000-0000-0000-000000000001"),
        ):
            MockLLM.return_value.invoke.return_value = MagicMock(
                content=json.dumps(llm_response)
            )
            normalize = make_normalize_node(config)
            result = normalize(_base_state([raw_doc_single]))

    assert len(result["transactions"]) == 1
    t = result["transactions"][0]
    assert t.merchant == "SUPER"
    assert t.amount_original == -100.0
    assert t.id is not None

    row = in_memory_session.execute(
        text("SELECT source_file, transactions_json FROM normalized_document_cache")
    ).fetchone()
    assert row is not None
    assert row[0] == "data/account.xlsx"
    stored = json.loads(row[1])
    assert len(stored) == 1
    assert stored[0]["merchant"] == "SUPER"


def test_normalize_invalid_json_skips_doc_continues(
    config, in_memory_session, raw_doc_single
) -> None:
    """LLM returns invalid JSON; doc is skipped, no exception, transactions empty."""
    with patch("agents.reconciliator.nodes.ChatOpenAI") as MockLLM:
        MockLLM.return_value.invoke.return_value = MagicMock(content="not valid json")
        normalize = make_normalize_node(config)
        result = normalize(_base_state([raw_doc_single]))

    assert result["transactions"] == []
    count = in_memory_session.execute(
        text("SELECT COUNT(*) FROM normalized_document_cache")
    ).scalar()
    assert count == 0


def test_normalize_accepts_dict_raw_documents(config, in_memory_session) -> None:
    """raw_documents may be dicts (e.g. from checkpoint); node converts to RawDocument."""
    raw_content = "| 2026-01-02 | COFFEE | -5 |"
    content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
    cached = [
        {
            "id": "from-dict",
            "date": "2026-01-02",
            "amount_original": -5.0,
            "amount_base": None,
            "currency": "USD",
            "merchant": "COFFEE",
            "account": "Savings",
            "source_file": "data/other.xlsx",
        }
    ]
    in_memory_session.execute(
        text(
            "INSERT INTO normalized_document_cache (source_file, content_hash, transactions_json)"
            " VALUES (:sf, :ch, :tj)"
        ),
        {"sf": "data/other.xlsx", "ch": content_hash, "tj": json.dumps(cached)},
    )
    in_memory_session.commit()

    state = _base_state(
        [
            {
                "source_file": "data/other.xlsx",
                "file_type": "xlsx",
                "content": raw_content,
            }
        ]
    )
    normalize = make_normalize_node(config)
    result = normalize(state)

    assert len(result["transactions"]) == 1
    assert result["transactions"][0].id == "from-dict"
    assert result["transactions"][0].merchant == "COFFEE"
