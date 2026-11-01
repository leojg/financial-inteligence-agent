"""Agent configuration (model, currency, categories)."""

import os
from dataclasses import dataclass, field


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, "").split(",") if v.strip())


@dataclass
class ReconciliationConfig:
    """Configuration for the reconciliation graph (LLM, base currency, categories)."""

    model_name: str = "gpt-5.4-mini"
    merchant_equivalence_model_name: str = "gpt-5.4-nano"
    vision_model_name: str = "claude-sonnet-4-6"
    vision_max_tokens: int = 4096
    temperature: float = 0.0
    base_currency: str = "USD"
    image_low_confidence_threshold: float = 0.98
    # Statements (v1.6). "legacy" keeps the per-page LLM normalization and only
    # measures it against the statement's balances; "ladder" is the v1.6 reader.
    statement_extractor: str = field(
        default_factory=lambda: os.getenv("STATEMENT_EXTRACTOR", "legacy")
    )
    statement_model_name: str = field(
        default_factory=lambda: os.getenv("STATEMENT_MODEL", "gpt-5.4-mini")
    )
    statement_header_llm: bool = True  # LLM fallback for header values the rules miss
    # What a bare "$" means in a statement header or column header.
    statement_local_currency: str = field(
        default_factory=lambda: os.getenv("STATEMENT_LOCAL_CURRENCY", "UYU")
    )
    # Passwords tried, in order, on encrypted statement PDFs (comma-separated env).
    pdf_passwords: tuple[str, ...] = field(
        default_factory=lambda: _env_list("STATEMENT_PDF_PASSWORDS")
    )


DEFAULT_CONFIG = ReconciliationConfig()
