"""Unit tests for the ingest node."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes import ingest


@pytest.fixture
def mock_documents():
    """Two fake LangChain-style documents as returned by load_documents."""
    doc1 = MagicMock()
    doc1.metadata = {"source": "/data/statements/account.xlsx"}
    doc1.page_content = "| Date | Description | Amount |\n| 2026-01-01 | SUPER | -100 |"
    doc2 = MagicMock()
    doc2.metadata = {"source": "/data/statements/other.pdf"}
    doc2.page_content = "Bank statement page 1"
    return [doc1, doc2]


@pytest.fixture
def minimal_state():
    """Minimal state required by ingest (only source_folder is read)."""
    return {
        "source_folder": "/data/statements",
        "raw_documents": [],
        "transactions": [],
        "duplicates": [],
        "suspicious": [],
        "exchange_rates": {},
        "report": None,
    }


def test_ingest_returns_raw_documents_from_loaded_files(
    minimal_state, mock_documents
):
    with patch("agent.nodes.load_documents", return_value=mock_documents):
        result = ingest(minimal_state)

    assert "raw_documents" in result
    raw = result["raw_documents"]
    assert len(raw) == 2

    assert raw[0].source_file == "/data/statements/account.xlsx"
    assert raw[0].file_type == "xlsx"
    assert "SUPER" in raw[0].content

    assert raw[1].source_file == "/data/statements/other.pdf"
    assert raw[1].file_type == "pdf"
    assert raw[1].content == "Bank statement page 1"


def test_ingest_calls_load_documents_with_source_folder_path(
    minimal_state, mock_documents
):
    with patch("agent.nodes.load_documents", return_value=mock_documents) as load:
        ingest(minimal_state)
    load.assert_called_once()
    call_arg = load.call_args[0][0]
    assert call_arg == Path("/data/statements")


def test_ingest_empty_folder_returns_empty_raw_documents(minimal_state):
    with patch("agent.nodes.load_documents", return_value=[]):
        result = ingest(minimal_state)
    assert result["raw_documents"] == []
