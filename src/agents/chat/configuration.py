"""Chat agent configuration."""

from dataclasses import dataclass


@dataclass
class ChatConfig:
    """Configuration for the chat graph."""

    model_name: str = "gpt-5.4"
    temperature: float = 0.2


DEFAULT_CONFIG = ChatConfig()
