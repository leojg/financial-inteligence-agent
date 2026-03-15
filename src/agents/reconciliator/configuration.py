"""Agent configuration (model, currency, categories)."""

from dataclasses import dataclass


@dataclass
class ReconciliationConfig:
    """Configuration for the reconciliation graph (LLM, base currency, categories)."""

    model_name: str = "gpt-4o-mini"
    vision_model_name: str = "claude-sonnet-4-6"
    vision_max_tokens: int = 4096
    temperature: float = 0.0
    base_currency: str = "USD"
    image_low_confidence_threshold: float = 0.98

DEFAULT_CONFIG = ReconciliationConfig()
