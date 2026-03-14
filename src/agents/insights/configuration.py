"""Insights agent configuration."""

from dataclasses import dataclass


@dataclass
class InsightsConfig:
    """Configuration for the insights graph."""

    model_name: str = "gpt-4o"
    temperature: float = 0.0


DEFAULT_CONFIG = InsightsConfig()
