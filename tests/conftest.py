"""Pytest configuration. Sets dummy OPENAI_API_KEY so agent package can be imported without a real key (unit tests mock the LLM)."""

import os

# Allow importing agent (and thus graph) during collection without a real API key
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
