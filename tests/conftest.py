"""Pytest configuration. Sets dummy OPENAI_API_KEY so agent package can be imported without a real key (unit tests mock the LLM)."""

from __future__ import annotations

import os

# Allow importing agent (and thus graph) during collection without a real API key
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import shared.db as _db
from shared.db.models import Base


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch shared.db to use a fresh in-memory SQLite for every test.

    StaticPool ensures all sessions within a test share the same connection,
    so data written in one get_session() call is visible in the next.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Factory: sessionmaker = sessionmaker(bind=engine)
    monkeypatch.setattr(_db, "_engine", engine)
    monkeypatch.setattr(_db, "_SessionFactory", Factory)
